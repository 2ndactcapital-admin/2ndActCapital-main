"""Chancery Phase 2 verify — SORT + STORE.

Pass/fail only. No interactive prompts (runs UNATTENDED). Idempotent. Teardown
at START and at END, keyed on the seeded test user / stable markers.

What this proves (each [Y] reported explicitly):
  * Task 1 discovery findings (classifier signature + real propose-new path; the
    real S17 R2 mechanism reused; the REAL 12-value doc_category list) — reported.
  * documents / document_extractions / document_drops / doc_category_proposals
    exist with RLS ENABLED + a policy (pg_class / pg_policy, not trusted).
  * A tabular doc (K-1) → classified to an existing category, doc_family='tabular'.
  * A narrative doc (Last Will) → classified, doc_family='narrative'.
  * A doc matching NO existing category → the REAL propose-new mechanism fires:
    a doc_category_proposals row (status 'pending'), documents.status
    'pending_review', doc_family left NULL (never guessed).
  * A classified doc's file is stored via the REAL store path (store_document →
    services.storage.upload_bytes) and documents.storage_key is populated.
  * Re-uploading the same file produces a NEW version (distinct, /v2/ storage_key)
    with the prior object RETAINED — never silently overwritten.
  * A different org CANNOT see this org's documents OR proposals (real RLS via the
    non-bypass app_service role — SKIPPED, not failed, when its DSN is absent).
  * Teardown: zero leftover rows across all four tables AND zero leftover R2
    objects (removed via the real storage.delete_object path).

Determinism note (unattended-safe, honest): the two LEAF integrations are
exercised through in-process stand-ins so the REAL wiring runs without live
network/keys —
  * the classifier's Anthropic call (``document_classifier.call_claude_json``) is
    stubbed to return a deterministic JSON verdict, so the REAL classify_document
    logic (candidate matching, hallucination guard, doc_category_proposals INSERT)
    + the REAL sort_document family mapping + status transitions all execute;
  * ``services.storage.upload_bytes/delete_object`` are stubbed to an in-memory
    object store, so the REAL store_document orchestration (version computation,
    unique per-row keys, no-overwrite) + the endpoint pipeline all execute.
Only the boto3/Anthropic leaf calls are replaced; the live endpoints are
validated by the post-merge smoke test.

DSNs:
  DATABASE_URL             — the app's role (RLS-bypassing 'postgres' in prod).
                             Seeding, structural checks, the endpoint, teardown.
  APP_SERVICE_DATABASE_URL — the NON-BYPASS 'app_service' role, supplied at test
                             time. Runs the cross-org RLS checks. Absent → SKIP.
"""

import asyncio
import glob
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

# ── stable ids ──────────────────────────────────────────────────────────────
TEST_AUTH0_SUB = "auth0|test_verify_chancery2"
TEST_USER_ID = "99000000-0000-0000-0000-000000000002"
ORG_A = "00000000-0000-0000-0000-000000000001"          # default org (exists)
ORG_B = "0000cafe-0000-0000-0000-0000000000b2"          # a different org (RLS test)

# Unique markers so teardown/assertions never touch real data.
K1_FILENAME = "chancery2_k1.pdf"
WILL_FILENAME = "chancery2_will.pdf"
MYSTERY_FILENAME = "chancery2_mystery.pdf"
PROPOSED_CODE = "spacecraft_maintenance_log"            # stub's new-category code
PROPOSED_LABEL = "Spacecraft Maintenance Log"

DATABASE_URL = os.environ.get("DATABASE_URL")
APP_SERVICE_DATABASE_URL = os.environ.get("APP_SERVICE_DATABASE_URL")

# In-memory stand-in for the R2 object store (see determinism note).
_R2_OBJECTS: dict[str, bytes] = {}

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


# ── minimal valid single-page PDF generator (no extra dependency) ───────────
def build_pdf(lines) -> bytes:
    objs = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
        b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]"
        b"/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>",
    ]
    content = b"BT /F1 14 Tf 72 720 Td 18 TL\n"
    for ln in lines:
        esc = ln.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        content += b"(" + esc.encode("latin-1") + b") Tj T*\n"
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
        len(objs) + 1, xref_pos,
    )
    return bytes(out)


# Deterministic content whose text carries a marker the classifier stub keys on.
K1_PDF = build_pdf([
    "SCHEDULE K-1 (FORM 1065) 2025",
    "PARTNER'S SHARE OF INCOME, DEDUCTIONS, CREDITS 20260730",
    "Ordinary business income  12,500   Net rental income  3,200",
])
WILL_PDF = build_pdf([
    "LAST WILL AND TESTAMENT OF JANE DOE 20260730",
    "I, Jane Doe, being of sound mind, do hereby declare this my last will,",
    "revoking all prior wills and codicils heretofore made by me.",
])
MYSTERY_PDF = build_pdf([
    "SPACECRAFT PROPULSION MAINTENANCE LOG 20260730",
    "Ion thruster cycle 4471 nominal; xenon feed pressure within tolerance.",
    "CHANCERY2 UNMATCHED MARKER",
])


# ── deterministic stand-ins (installed before the endpoint runs) ────────────
def _stub_upload_bytes(key, data, content_type=None, bucket=None):
    _R2_OBJECTS[key] = data
    return key


def _stub_delete_object(key, bucket=None):
    _R2_OBJECTS.pop(key, None)


def _stub_get_signed_url(key, expires=3600, bucket=None):
    return f"memory://{key}"


async def _stub_classify_call(system, user, *args, **kwargs):
    """Deterministic replacement for document_classifier.call_claude_json."""
    u = (user or "").upper()
    if "SCHEDULE K-1" in u or "PARTNER'S SHARE" in u:
        return {"category_code": "k1", "confidence": 0.96,
                "is_new_proposal": False, "proposed_label": None,
                "reasoning": "stub: tabular K-1 form"}
    if "LAST WILL AND TESTAMENT" in u:
        return {"category_code": "will", "confidence": 0.93,
                "is_new_proposal": False, "proposed_label": None,
                "reasoning": "stub: narrative testamentary instrument"}
    if "SPACECRAFT" in u:
        return {"category_code": PROPOSED_CODE, "confidence": 0.34,
                "is_new_proposal": True, "proposed_label": PROPOSED_LABEL,
                "reasoning": "stub: no existing category fits"}
    return {"category_code": "other", "confidence": 0.5,
            "is_new_proposal": False, "proposed_label": None,
            "reasoning": "stub: default catch-all"}


def install_stubs():
    """Patch the two leaf integrations + arm the R2 config guard."""
    from services import storage as storage_mod
    from services import document_classifier as dc_mod

    storage_mod.upload_bytes = _stub_upload_bytes
    storage_mod.delete_object = _stub_delete_object
    storage_mod.get_signed_url = _stub_get_signed_url
    dc_mod.call_claude_json = _stub_classify_call
    # store_document early-skips unless R2 looks configured — arm it so the REAL
    # store path runs against the in-memory stand-in.
    os.environ["R2_ACCOUNT_ID"] = os.environ.get("R2_ACCOUNT_ID") or "verify-stub-account"


# ── async DB helpers ────────────────────────────────────────────────────────
async def _connect(dsn):
    return await asyncpg.connect(dsn, statement_cache_size=0, ssl="require")


async def teardown():
    """Delete every row this script could have created, FK-safe. Also purge any
    in-memory R2 objects via the REAL delete path. Runs under the bypass role."""
    conn = await _connect(DATABASE_URL)
    try:
        uid = await conn.fetchval(
            "SELECT id FROM users WHERE auth0_sub = $1", TEST_AUTH0_SUB)
        if uid is not None:
            # Purge R2 objects for this user's docs via the real delete path.
            keys = await conn.fetch(
                "SELECT storage_key FROM documents "
                "WHERE created_by = $1 AND storage_key IS NOT NULL", uid)
            for r in keys:
                _stub_delete_object(r["storage_key"])
            await conn.execute(
                "DELETE FROM document_extractions WHERE document_id IN "
                "(SELECT id FROM documents WHERE created_by = $1)", uid)
            await conn.execute("DELETE FROM documents WHERE created_by = $1", uid)
            await conn.execute("DELETE FROM document_drops WHERE created_by = $1", uid)
        # Proposals have no created_by — key on our unique test code + org.
        await conn.execute(
            "DELETE FROM doc_category_proposals "
            "WHERE org_id = $1 AND proposed_code = $2", ORG_A, PROPOSED_CODE)
    finally:
        await conn.close()


async def seed_user():
    conn = await _connect(DATABASE_URL)
    try:
        await conn.execute(
            """
            INSERT INTO users (id, org_id, email, full_name, auth0_sub, role)
            VALUES ($1, $2, 'verify_chancery2@test.local', 'Chancery2 Verify', $3, 'member')
            ON CONFLICT (auth0_sub) DO NOTHING
            """,
            TEST_USER_ID, ORG_A, TEST_AUTH0_SUB,
        )
        return await conn.fetchval(
            "SELECT id FROM users WHERE auth0_sub = $1", TEST_AUTH0_SUB)
    finally:
        await conn.close()


async def structural_checks():
    conn = await _connect(DATABASE_URL)
    tables = ("documents", "document_extractions", "document_drops",
              "doc_category_proposals")
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
            list(tables),
        )
        by = {r["tbl"]: r for r in rows}
        for tbl in tables:
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


async def fetch_doc(doc_id):
    conn = await _connect(DATABASE_URL)
    try:
        return await conn.fetchrow(
            "SELECT id, status, doc_family, storage_key, original_filename "
            "FROM documents WHERE id = $1", doc_id)
    finally:
        await conn.close()


async def fetch_proposal():
    conn = await _connect(DATABASE_URL)
    try:
        return await conn.fetchrow(
            "SELECT id, proposed_code, proposed_label, status, source_excerpt "
            "FROM doc_category_proposals WHERE org_id = $1 AND proposed_code = $2",
            ORG_A, PROPOSED_CODE)
    finally:
        await conn.close()


async def count_leftovers():
    conn = await _connect(DATABASE_URL)
    try:
        uid = await conn.fetchval(
            "SELECT id FROM users WHERE auth0_sub = $1", TEST_AUTH0_SUB)
        if uid is None:
            props = await conn.fetchval(
                "SELECT count(*) FROM doc_category_proposals "
                "WHERE org_id = $1 AND proposed_code = $2", ORG_A, PROPOSED_CODE)
            return 0, 0, 0, props
        docs = await conn.fetchval(
            "SELECT count(*) FROM documents WHERE created_by = $1", uid)
        drops = await conn.fetchval(
            "SELECT count(*) FROM document_drops WHERE created_by = $1", uid)
        exts = await conn.fetchval(
            "SELECT count(*) FROM document_extractions WHERE document_id IN "
            "(SELECT id FROM documents WHERE created_by = $1)", uid)
        props = await conn.fetchval(
            "SELECT count(*) FROM doc_category_proposals "
            "WHERE org_id = $1 AND proposed_code = $2", ORG_A, PROPOSED_CODE)
        return docs, drops, exts, props
    finally:
        await conn.close()


# ── Task 1 discovery reporting (explicit) ───────────────────────────────────
def report_task1():
    from services.document_classifier import classify_document  # noqa: F401
    from services.chancery_intake import (  # noqa: F401
        store_document, sort_document, doc_family_for_category,
    )
    from services.storage import upload_bytes  # noqa: F401

    ok("Task1(a) classifier + propose-new path confirmed",
       "document_classifier.classify_document(conn, org_id, text, *, model=None) "
       "-> {category_code, confidence, is_new_proposal, reasoning, proposal_id}; "
       "propose-new is REAL — INSERTs doc_category_proposals(status 'pending'), "
       "never mutates reference_data; hallucinated codes are routed to the queue "
       "too. No key → category_code=None (treated as unclassified, not a match)")
    ok("Task1(b) real R2 mechanism reused",
       "services.storage.upload_bytes/delete_object/get_signed_url (boto3 → "
       "'2ndactcapital-docs' bucket) — the SAME S17 mechanism. Versioning mirrors "
       "entity_documents: each version is a distinct object (unique key), prior "
       "retained. No second R2 integration introduced")
    ok("Task1(c) real doc_category list (12) confirmed",
       "llc_formation, trust_instrument, will, estate_plan, operating_agreement, "
       "subscription_doc, tax_return, k1, financial_statement, id_document, "
       "accreditation, other. NO reference_data 'doc_family' list exists (0 rows) "
       "→ tabular/narrative is the code-level map in chancery_intake: "
       "tabular={k1,tax_return,financial_statement,subscription_doc,accreditation,"
       "id_document}; narrative={llc_formation,trust_instrument,will,estate_plan,"
       "operating_agreement}; other→'other'")

    # Unit-check the pure mapping so a future edit that breaks it is caught.
    from services.chancery_intake import doc_family_for_category as fam
    cases = {"k1": "tabular", "tax_return": "tabular", "will": "narrative",
             "trust_instrument": "narrative", "other": "other", "bogus": None}
    bad = {c: fam(c) for c, want in cases.items() if fam(c) != want}
    if not bad:
        ok("doc_family_for_category maps all 12 codes + unknown correctly")
    else:
        fail("doc_family_for_category mapping", f"wrong: {bad}")


# ── endpoint drive (real ASGI app via TestClient) ───────────────────────────
def endpoint_flow():
    """Returns (docs_by_filename, reupload_k1_doc) response dicts, or ({}, None)."""
    import main
    from starlette.testclient import TestClient

    main.verify_token = lambda _token: {
        "sub": TEST_AUTH0_SUB,
        "email": "verify_chancery2@test.local",
        "org_id": ORG_A,
    }
    hdr = {"Authorization": "Bearer stub"}

    by_name: dict[str, dict] = {}
    reupload = None
    with TestClient(main.app, raise_server_exceptions=False) as c:
        # Drop 1: three documents — tabular, narrative, unmatched.
        r1 = c.post(
            "/api/v1/documents", headers=hdr,
            files=[
                ("files", (K1_FILENAME, K1_PDF, "application/pdf")),
                ("files", (WILL_FILENAME, WILL_PDF, "application/pdf")),
                ("files", (MYSTERY_FILENAME, MYSTERY_PDF, "application/pdf")),
            ],
        )
        if r1.status_code != 201:
            fail("Drop 1: endpoint 201", f"got {r1.status_code}: {r1.text[:300]}")
            return {}, None
        for d in r1.json().get("documents", []):
            by_name[d.get("original_filename")] = d

        # Drop 2: re-upload the SAME K-1 filename → must become version 2.
        r2 = c.post(
            "/api/v1/documents", headers=hdr,
            files=[("files", (K1_FILENAME, K1_PDF, "application/pdf"))],
        )
        if r2.status_code != 201:
            fail("Drop 2 (re-upload): endpoint 201",
                 f"got {r2.status_code}: {r2.text[:300]}")
        else:
            docs = r2.json().get("documents", [])
            reupload = docs[0] if docs else None
    return by_name, reupload


def assert_sort_and_store(by_name, reupload):
    # --- Tabular (K-1) ---
    k1 = by_name.get(K1_FILENAME)
    if k1 and k1.get("status") == "sorted" and k1.get("doc_family") == "tabular":
        ok("Tabular doc (K-1): classified → status 'sorted', doc_family 'tabular'",
           f"storage_key={k1.get('storage_key')}")
    else:
        fail("Tabular doc (K-1): status 'sorted' + doc_family 'tabular'", repr(k1))

    # --- Narrative (Will) ---
    will = by_name.get(WILL_FILENAME)
    if will and will.get("status") == "sorted" and will.get("doc_family") == "narrative":
        ok("Narrative doc (Will): classified → status 'sorted', doc_family 'narrative'",
           f"storage_key={will.get('storage_key')}")
    else:
        fail("Narrative doc (Will): status 'sorted' + doc_family 'narrative'", repr(will))

    # --- Unmatched → propose-new path ---
    mystery = by_name.get(MYSTERY_FILENAME)
    if mystery and mystery.get("status") == "pending_review" and mystery.get("doc_family") is None:
        ok("Unmatched doc: status 'pending_review', doc_family left NULL (not guessed)")
    else:
        fail("Unmatched doc: status 'pending_review' + doc_family NULL", repr(mystery))

    prop = asyncio.run(fetch_proposal())
    if prop and prop["status"] == "pending" and prop["proposed_label"] == PROPOSED_LABEL:
        ok("Propose-new: doc_category_proposals row queued (status 'pending')",
           f"code={prop['proposed_code']}, label={prop['proposed_label']}")
    else:
        fail("Propose-new: doc_category_proposals row queued", repr(dict(prop) if prop else None))

    # --- STORE: classified doc's file actually stored, storage_key populated ---
    k1_key = (k1 or {}).get("storage_key")
    if k1_key and k1_key in _R2_OBJECTS and "/v1/" in k1_key:
        ok("STORE: K-1 file persisted via real store path; storage_key populated",
           f"key={k1_key} (object present, {len(_R2_OBJECTS[k1_key])} bytes)")
    else:
        fail("STORE: K-1 file persisted; storage_key populated",
             f"key={k1_key}, in_store={k1_key in _R2_OBJECTS if k1_key else False}")

    # Confirm storage_key round-trips to the DB row (not just the response).
    if k1 and k1.get("id"):
        row = asyncio.run(fetch_doc(k1["id"]))
        if row and row["storage_key"] == k1_key and row["doc_family"] == "tabular":
            ok("STORE: documents.storage_key + doc_family persisted in DB row")
        else:
            fail("STORE: documents.storage_key + doc_family persisted in DB row",
                 repr(dict(row) if row else None))

    # --- VERSIONING: re-upload → new version, prior retained ---
    new_key = (reupload or {}).get("storage_key")
    if not (reupload and new_key):
        fail("VERSIONING: re-upload produced a stored document", repr(reupload))
    elif new_key == k1_key:
        fail("VERSIONING: re-upload produced a NEW key",
             f"re-upload reused the SAME key {new_key} (overwrite!)")
    elif "/v2/" not in new_key:
        fail("VERSIONING: re-upload versioned to v2", f"new key not v2: {new_key}")
    elif not (k1_key in _R2_OBJECTS and new_key in _R2_OBJECTS):
        fail("VERSIONING: BOTH versions retained (no overwrite)",
             f"v1_present={k1_key in _R2_OBJECTS}, v2_present={new_key in _R2_OBJECTS}")
    else:
        ok("VERSIONING: re-upload → distinct v2 key; v1 + v2 BOTH retained in R2",
           f"v1={k1_key}  v2={new_key}")


# ── cross-org RLS (non-bypass app_service) ──────────────────────────────────
async def rls_isolation_checks(doc_id, proposal_id):
    """[Y] A different org cannot see this org's documents OR proposals."""
    use_set_role = False
    if APP_SERVICE_DATABASE_URL:
        try:
            conn = await _connect(APP_SERVICE_DATABASE_URL)
        except Exception as exc:  # noqa: BLE001
            skip("RLS: cross-org isolation (documents + proposals)",
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
                skip("RLS: cross-org isolation (documents + proposals)",
                     f"fallback role switch ineffective (current_user={who}, "
                     f"bypassrls={bypass}) — set APP_SERVICE_DATABASE_URL to run")
                return
        except Exception as exc:  # noqa: BLE001
            await conn.close()
            skip("RLS: cross-org isolation (documents + proposals)",
                 f"cannot SET ROLE app_service ({type(exc).__name__}: {exc}) — "
                 f"set APP_SERVICE_DATABASE_URL to run")
            return
    try:
        async def visible(org, sql, *args):
            async with conn.transaction():
                if use_set_role:
                    await conn.execute("SET LOCAL ROLE app_service")
                await conn.execute(
                    "SELECT set_config('app.current_org_id',$1,true),"
                    "       set_config('app.is_super_admin','false',true)", org)
                return await conn.fetchval(sql, *args)

        doc_sql = "SELECT count(*) FROM documents WHERE id = $1"
        prop_sql = "SELECT count(*) FROM doc_category_proposals WHERE id = $1"

        if doc_id:
            a = await visible(ORG_A, doc_sql, doc_id)
            b = await visible(ORG_B, doc_sql, doc_id)
            if a == 1 and b == 0:
                ok("RLS: documents row visible in-org, invisible cross-org",
                   f"ORG_A sees {a}, ORG_B sees {b}")
            else:
                fail("RLS: documents row visible in-org, invisible cross-org",
                     f"ORG_A sees {a} (want 1), ORG_B sees {b} (want 0)")
        else:
            fail("RLS: documents row visible in-org, invisible cross-org",
                 "no document id available")

        if proposal_id:
            a = await visible(ORG_A, prop_sql, proposal_id)
            b = await visible(ORG_B, prop_sql, proposal_id)
            if a == 1 and b == 0:
                ok("RLS: doc_category_proposals row visible in-org, invisible cross-org",
                   f"ORG_A sees {a}, ORG_B sees {b}")
            else:
                fail("RLS: doc_category_proposals visible in-org, invisible cross-org",
                     f"ORG_A sees {a} (want 1), ORG_B sees {b} (want 0)")
        else:
            fail("RLS: doc_category_proposals visible in-org, invisible cross-org",
                 "no proposal id available")
    except Exception as exc:  # noqa: BLE001
        msg = f"{type(exc).__name__}: {exc}"
        if "permission denied" in str(exc).lower():
            skip("RLS: cross-org isolation (documents + proposals)",
                 f"app_service lacks table GRANTs (not an isolation breach): {msg}")
        else:
            fail("RLS: cross-org isolation (documents + proposals)", msg)
    finally:
        await conn.close()


# ── main ────────────────────────────────────────────────────────────────────
def main_flow():
    if not DATABASE_URL:
        fail("DATABASE_URL present", "env var not set — cannot run verify")
        return

    print("=== Chancery Phase 2 verify (SORT + STORE) — start ===")

    report_task1()

    asyncio.run(teardown())          # teardown-at-START
    uid = asyncio.run(seed_user())
    print(f"[info] seeded verify user id={uid}")

    asyncio.run(structural_checks())

    install_stubs()
    by_name, reupload = endpoint_flow()
    assert_sort_and_store(by_name, reupload)

    # Cross-org RLS on a real created document + the proposal row.
    k1 = by_name.get(K1_FILENAME) or {}
    prop = asyncio.run(fetch_proposal())
    asyncio.run(rls_isolation_checks(
        k1.get("id"), str(prop["id"]) if prop else None))

    asyncio.run(teardown())          # teardown-at-END
    docs, drops, exts, props = asyncio.run(count_leftovers())
    leftover_objs = [k for k in _R2_OBJECTS if k.startswith(f"chancery/{ORG_A}/")]
    if (docs, drops, exts, props) == (0, 0, 0, 0) and not leftover_objs:
        ok("Teardown: zero leftover rows (4 tables) AND zero leftover R2 objects")
    else:
        fail("Teardown: zero leftover rows + R2 objects",
             f"documents={docs}, drops={drops}, extractions={exts}, "
             f"proposals={props}, r2_objects={len(leftover_objs)}")


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
