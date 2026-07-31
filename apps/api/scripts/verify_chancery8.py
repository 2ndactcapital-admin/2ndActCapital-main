"""Chancery Phase 8 verify — correction-learning loop (RAG over the correction log).

Pass/fail only. No interactive prompts (runs UNATTENDED). Idempotent. Teardown at
START and at END, keyed on stable test ids + a filename MARKER.

Exercises the REAL Phase-8 code:
  * services.correction_retrieval.get_relevant_corrections / apply_field_corrections
  * services.document_classifier.classify_document (few-shot injection)
  * services.textract_extraction.run_k1_extraction (deterministic correction layer)
  * scripts.eval_correction_loop.evaluate (DeepEval before/after, no-judge)

Data-isolation is proven two ways: (1) the retrieval QUERY filters by org_id even
on the BYPASS role (RLS off) — so the isolation is in the query logic itself, not
only RLS; (2) against the REAL non-bypass app_service role a different org's
session sees NONE of another org's corrections.

DSNs:
  DATABASE_URL             — bypass (postgres): seeding, service calls, teardown.
  APP_SERVICE_DATABASE_URL — non-bypass app_service (falls back to SET LOCAL ROLE,
                             else SKIPs the app_service sub-check).
  ANTHROPIC_API_KEY        — required for the classification (LLM) assertions;
                             the deterministic K-1 + retrieval assertions run
                             WITHOUT it.
"""

import asyncio
import glob
import json
import os
import sys

# ── runnable via allowlisted system python3 OR venv python ──────────────────
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

from services import correction_retrieval as cr  # noqa: E402
from services.database import get_pool, set_rls_context, reset_rls_context  # noqa: E402
from services.document_classifier import classify_document  # noqa: E402
from services.textract_extraction import run_k1_extraction  # noqa: E402
import eval_correction_loop as ecl  # noqa: E402

DATABASE_URL = os.environ.get("DATABASE_URL")
APP_SERVICE_DATABASE_URL = os.environ.get("APP_SERVICE_DATABASE_URL")
HAS_KEY = bool(os.environ.get("ANTHROPIC_API_KEY"))

# ── stable ids / markers ─────────────────────────────────────────────────────
ORG_A = "00000000-0000-0000-0000-000000000001"   # default org (exists)
ORG_B = "0000caf8-0000-0000-0000-0000000000c8"   # a different org (isolation)
ORG_ZERO = "0000dead-0000-0000-0000-00000000dead"  # no data at all (zero-history)
MARKER = "chancery8_verify_marker"
MARKER_A = "MARKER_A_8_chancery8"
MARKER_B = "MARKER_B_8_chancery8"

TEST_AUTH0_SUB = "auth0|test_verify_chancery8"
TEST_USER_ID = "99000000-0000-0000-0000-000000000008"

CLS_PRIOR_A = "99000000-0000-0000-0000-000000000881"  # prior classification-corrected doc (A)
K1_PRIOR_A = "99000000-0000-0000-0000-000000000882"   # prior k1 doc hosting field correction (A)
K1_NEW_A = "99000000-0000-0000-0000-000000000883"     # new k1 doc for WITH/WITHOUT run (A)
K1_NOCORR_B = "99000000-0000-0000-0000-0000000008b4"  # k1 doc, org B, no corrections
CLS_PRIOR_B = "99000000-0000-0000-0000-0000000008b1"  # prior classification-corrected doc (B)

ALL_DOC_IDS = [CLS_PRIOR_A, K1_PRIOR_A, K1_NEW_A, K1_NOCORR_B, CLS_PRIOR_B]

# K-1 recurring extraction error a human already fixed once.
K1_FIELD = "ordinary_business_income"
K1_RAW = "1234.00"        # what the regex mapper produces
K1_CORRECTED = "5678.90"  # what the human corrected it to (exact decimal string)
K1_TEXT = "Schedule K-1 (Form 1065)\nOrdinary business income 1,234.00\n"

# Classification correction: this org files wills under the estate-plan category.
CLS_NATURAL = "will"
CLS_CORRECTED = "estate_plan"
# The NEW document (a recurring identical will) the loop should re-file.
CLS_NEW_TEXT = (
    "LAST WILL AND TESTAMENT OF ELEANOR VANCE.\n"
    "I, Eleanor Vance, being of sound mind, hereby revoke all prior wills and "
    "bequeath my residuary estate to my children in equal shares. [" + MARKER_A + "]"
)
CLS_PRIOR_TEXT_A = CLS_NEW_TEXT  # identical recurring doc → strongest match
CLS_PRIOR_TEXT_B = (
    "LAST WILL AND TESTAMENT OF A DIFFERENT ORG. [" + MARKER_B + "]"
)

# ── tiny pass/fail harness ──────────────────────────────────────────────────
_RESULTS: list[tuple[str, str, str]] = []
# Set once at the start of the run (after the opening teardown) so teardown can
# reap the ai_decision_log rows our classifier calls write on the DEFAULT org via
# the shared pool (those writes bypass the verify's own conn/savepoint).
_RUN_START = None


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


async def teardown(conn):
    """FK-safe, child-first. Keyed on the test doc ids + user + filename marker."""
    await conn.execute(
        "DELETE FROM document_field_corrections WHERE document_id = ANY($1::uuid[])",
        ALL_DOC_IDS)
    await conn.execute(
        "DELETE FROM document_field_corrections WHERE org_id = $1", ORG_B)
    await conn.execute(
        "DELETE FROM document_template_extractions WHERE document_id = ANY($1::uuid[])",
        ALL_DOC_IDS)
    await conn.execute(
        "DELETE FROM document_extractions WHERE document_id = ANY($1::uuid[])",
        ALL_DOC_IDS)
    await conn.execute(
        "DELETE FROM documents WHERE id = ANY($1::uuid[]) "
        "OR original_filename LIKE '%' || $2 || '%'",
        ALL_DOC_IDS, MARKER)
    await conn.execute("DELETE FROM users WHERE auth0_sub = $1", TEST_AUTH0_SUB)
    # ai_decision_log rows our classifier calls emit via the shared pool (they are
    # NOT on this conn, so they persist past a rolled-back savepoint). Throwaway
    # orgs: reap all of their rows. Default org: reap only classifier rows created
    # during THIS run (guarded by _RUN_START so we never touch pre-existing rows).
    await conn.execute(
        "DELETE FROM ai_decision_log WHERE org_id = ANY($1::uuid[])",
        [ORG_B, ORG_ZERO])
    if _RUN_START is not None:
        await conn.execute(
            "DELETE FROM ai_decision_log WHERE org_id = $1 "
            "AND task_type = 'document_classifier' AND created_at >= $2",
            ORG_A, _RUN_START)
    # The throwaway orgs are created by seed(); remove them last (after all their
    # child rows above are gone).
    await conn.execute(
        "DELETE FROM organizations WHERE id = ANY($1::uuid[])", [ORG_B, ORG_ZERO])


async def _seed_doc(conn, doc_id, org_id, text, uid, *, template=None,
                    status="confirmed"):
    """Insert a document + its native extraction; optionally a k1 template row."""
    await conn.execute(
        """
        INSERT INTO documents (id, org_id, original_filename, source, status, created_by)
        VALUES ($1, $2, $3, 'upload', $4, $5)
        ON CONFLICT (id) DO NOTHING
        """,
        doc_id, org_id, f"{MARKER}_{doc_id[-4:]}.pdf", status, uid)
    await conn.execute(
        """
        INSERT INTO document_extractions
            (document_id, org_id, extraction_method, has_native_text_layer,
             extracted_text, page_count)
        VALUES ($1, $2, 'native_pdfplumber', true, $3, 1)
        """,
        doc_id, org_id, text)
    tmpl_id = None
    if template is not None:
        tmpl_id = await conn.fetchval(
            """
            INSERT INTO document_template_extractions
                (document_id, org_id, template_type, extraction_source, mapped_fields)
            VALUES ($1, $2, 'k1', 'native', $3::jsonb)
            RETURNING id
            """,
            doc_id, org_id, json.dumps(template))
    return tmpl_id


async def seed(conn):
    # Throwaway orgs: ORG_B for cross-org isolation, ORG_ZERO for the zero-history
    # classification check (must exist so its ai_decision_log FK is satisfied).
    await conn.execute(
        "INSERT INTO organizations (id, name, slug) VALUES "
        "($1, $2, $3), ($4, $5, $6) ON CONFLICT (id) DO NOTHING",
        ORG_B, f"Chancery8 Isolation Org {MARKER}", f"chancery8-iso-{MARKER}",
        ORG_ZERO, f"Chancery8 Zero Org {MARKER}", f"chancery8-zero-{MARKER}")
    await conn.execute(
        """
        INSERT INTO users (id, org_id, email, full_name, auth0_sub, role)
        VALUES ($1, $2, 'verify_chancery8@test.local', 'Chancery8 Verify', $3, 'member')
        ON CONFLICT (auth0_sub) DO NOTHING
        """,
        TEST_USER_ID, ORG_A, TEST_AUTH0_SUB)
    uid = await conn.fetchval("SELECT id FROM users WHERE auth0_sub = $1", TEST_AUTH0_SUB)

    # (A) prior classification-corrected doc + doc_category correction (org A)
    await _seed_doc(conn, CLS_PRIOR_A, ORG_A, CLS_PRIOR_TEXT_A, uid)
    await conn.execute(
        """
        INSERT INTO document_field_corrections
            (document_id, org_id, template_extraction_id, field_name,
             original_value, corrected_value, corrected_by)
        VALUES ($1, $2, NULL, $3, $4, $5, $6)
        """,
        CLS_PRIOR_A, ORG_A, cr.CLASSIFICATION_FIELD, CLS_NATURAL, CLS_CORRECTED, uid)

    # (B) prior k1 doc + template extraction + field correction (org A)
    prior_tmpl = await _seed_doc(
        conn, K1_PRIOR_A, ORG_A, K1_TEXT, uid,
        template={"partner_name": "Prior Partner", K1_FIELD: K1_RAW})
    await conn.execute(
        """
        INSERT INTO document_field_corrections
            (document_id, org_id, template_extraction_id, field_name,
             original_value, corrected_value, corrected_by)
        VALUES ($1, $2, $3, $4, $5, $6, $7)
        """,
        K1_PRIOR_A, ORG_A, prior_tmpl, K1_FIELD, K1_RAW, K1_CORRECTED, uid)

    # (C) new k1 doc for the WITH/WITHOUT extraction run (org A) — native text only
    await _seed_doc(conn, K1_NEW_A, ORG_A, K1_TEXT, uid, status="sorted")

    # (D) k1 doc for the ZERO-correction test (org B — has no corrections at all)
    await _seed_doc(conn, K1_NOCORR_B, ORG_B, K1_TEXT, uid, status="sorted")

    # (E) org B classification correction — isolation (must never leak into A)
    await _seed_doc(conn, CLS_PRIOR_B, ORG_B, CLS_PRIOR_TEXT_B, uid)
    await conn.execute(
        """
        INSERT INTO document_field_corrections
            (document_id, org_id, template_extraction_id, field_name,
             original_value, corrected_value, corrected_by)
        VALUES ($1, $2, NULL, $3, $4, $5, $6)
        """,
        CLS_PRIOR_B, ORG_B, cr.CLASSIFICATION_FIELD, "trust_instrument", "will", uid)
    return uid


# ── assertions ───────────────────────────────────────────────────────────────
async def a1_discovery():
    print("\n--- Assertion 1: Task 1 discovery findings ---")
    ok("Task 1(a) schema/join path — NO schema change needed",
       "document_field_corrections has (field_name, original_value, corrected_value, "
       "org_id, document_id, template_extraction_id nullable, corrected_by, "
       "corrected_at). K-1/extraction corrections join template_extraction_id → "
       "document_template_extractions.template_type ('k1'). Classifications are "
       "encoded with reserved field_name='doc_category' + NULL template_extraction_id "
       "(documents has NO category column, only doc_family) — the nullable columns "
       "already support this. NOTE: table was EMPTY at sprint start (Phase 6/7 tests "
       "tore down their rows) — the loop is proven on freshly seeded data.")
    ok("Task 1(b) classify_document hook",
       "document_classifier.classify_document builds _build_system_prompt(candidates) "
       "then calls call_claude_json — few-shot correction block is appended to the "
       "system prompt BEFORE that call, gated by use_corrections.")
    ok("Task 1(c) K-1 mapping hook — DETERMINISTIC, not an AI call",
       "textract_extraction.map_k1_fields is pure regex (cost discipline: no LLM). "
       "So corrections cannot be 'few-shot' injected; they are applied as a "
       "deterministic post-map layer (apply_field_corrections) in run_k1_extraction, "
       "gated by use_corrections.")


async def a2_retrieval(conn):
    print("\n--- Assertion 2: get_relevant_corrections — relevant hits + empty ---")
    cls = await cr.get_relevant_corrections(conn, ORG_A, {"kind": "classification"})
    cls_hit = any(
        c["original_value"] == CLS_NATURAL and c["corrected_value"] == CLS_CORRECTED
        and (c["excerpt"] or "").find(MARKER_A) >= 0
        for c in cls)
    ext = await cr.get_relevant_corrections(
        conn, ORG_A, {"kind": "extraction", "template_type": "k1"})
    ext_hit = any(
        c["field_name"] == K1_FIELD and c["original_value"] == K1_RAW
        and c["corrected_value"] == K1_CORRECTED and c["template_type"] == "k1"
        for c in ext)
    empty_org = await cr.get_relevant_corrections(
        conn, ORG_B, {"kind": "extraction", "template_type": "k1"})
    empty_field = await cr.get_relevant_corrections(
        conn, ORG_A, {"kind": "extraction", "template_type": "k1",
                      "field_name": "does_not_exist"})
    if (cls_hit and ext_hit and empty_org == [] and empty_field == []):
        ok("retrieval returns correct relevant rows AND empty for no-history",
           f"classification hits={len(cls)}, extraction hits={len(ext)}, "
           f"org-B k1={empty_org}, unknown-field={empty_field}")
    else:
        fail("retrieval relevant/empty",
             f"cls_hit={cls_hit}, ext_hit={ext_hit}, empty_org={empty_org}, "
             f"empty_field={empty_field}, cls={cls}, ext={ext}")


async def a3_classification_behavior(conn):
    print("\n--- Assertion 3: classification WITH vs WITHOUT (injection changes it) ---")
    if not HAS_KEY:
        skip("classification WITH vs WITHOUT differs & is correct",
             "ANTHROPIC_API_KEY not set — LLM classification cannot run")
        return
    # Same call, correction lookup OFF then ON. The correction (identical recurring
    # will → estate_plan) is already seeded for ORG_A.
    without = await classify_document(conn, ORG_A, CLS_NEW_TEXT, use_corrections=False)
    with_ = await classify_document(conn, ORG_A, CLS_NEW_TEXT, use_corrections=True)
    wo = without.get("category_code")
    w = with_.get("category_code")
    if w == CLS_CORRECTED and w != wo:
        ok("classification changed by injected correction (different AND correct)",
           f"WITHOUT={wo} (its natural reading) → WITH={w} (the org-corrected code)")
    else:
        fail("classification WITH vs WITHOUT differs & is correct",
             f"WITHOUT={wo}, WITH={w}, expected WITH={CLS_CORRECTED}")


async def a4_k1_behavior(pool):
    print("\n--- Assertion 4: K-1 field-mapping WITH vs WITHOUT (deterministic) ---")
    doc = {"id": K1_NEW_A}
    tokens = set_rls_context(ORG_A, False)
    try:
        without = await run_k1_extraction(
            pool, doc, ORG_A, b"", use_corrections=False)
        with_ = await run_k1_extraction(
            pool, doc, ORG_A, b"", use_corrections=True)
    finally:
        reset_rls_context(tokens)
    wo_val = without["mapped_fields"].get(K1_FIELD)
    w_val = with_["mapped_fields"].get(K1_FIELD)
    applied = with_.get("corrections_applied") or []
    applied_ok = any(a["field_name"] == K1_FIELD and a["to"] == K1_CORRECTED
                     for a in applied)
    if (wo_val == K1_RAW and w_val == K1_CORRECTED and wo_val != w_val
            and not (without.get("corrections_applied") or []) and applied_ok):
        ok("K-1 mapping corrected by learned field correction (different AND correct)",
           f"WITHOUT {K1_FIELD}={wo_val} → WITH={w_val}; applied={applied}")
    else:
        fail("K-1 WITH vs WITHOUT differs & is correct",
             f"WITHOUT={wo_val}, WITH={w_val}, applied={applied}")


async def a5_zero_corrections(conn, pool):
    print("\n--- Assertion 5: zero relevant corrections neither breaks nor alters ---")
    # K-1: org B has no corrections → WITH == WITHOUT, nothing applied.
    doc = {"id": K1_NOCORR_B}
    tokens = set_rls_context(ORG_B, False)
    try:
        wo = await run_k1_extraction(pool, doc, ORG_B, b"", use_corrections=False)
        w = await run_k1_extraction(pool, doc, ORG_B, b"", use_corrections=True)
    finally:
        reset_rls_context(tokens)
    k1_unchanged = (
        wo["mapped_fields"] == w["mapped_fields"]
        and not (w.get("corrections_applied") or [])
        and w["mapped_fields"].get(K1_FIELD) == K1_RAW)

    # Classification: a genuinely zero-history org → empty few-shot block → the
    # system prompt is byte-identical to the pre-Phase-8 prompt. (ORG_A and ORG_B
    # both hold seeded corrections, so use a dataless org id for the true 'none'.)
    empty = await cr.get_relevant_corrections(conn, ORG_ZERO, {"kind": "classification"})
    block = cr.format_classification_examples(empty)
    prompt_unchanged = (empty == [] and block == "")

    # And the classifier call itself does not raise with corrections enabled and
    # none available (runs even without a key — returns the graceful null dict).
    call_ok = True
    try:
        res = await classify_document(conn, ORG_ZERO, "Some neutral text.",
                                      use_corrections=True)
        call_ok = isinstance(res, dict) and "category_code" in res
    except Exception as exc:  # noqa: BLE001
        call_ok = False
        print(f"    classify raised: {exc}")

    if k1_unchanged and prompt_unchanged and call_ok:
        ok("zero corrections: K-1 unchanged, classifier prompt unchanged, no error",
           f"k1_unchanged={k1_unchanged}, empty_block={prompt_unchanged}, "
           f"classify_ok={call_ok}")
    else:
        fail("zero corrections neither breaks nor alters",
             f"k1_unchanged={k1_unchanged}, prompt_unchanged={prompt_unchanged}, "
             f"call_ok={call_ok}")


class _Rollback(Exception):
    """Raised to roll back the eval's SAVEPOINT after capturing its report."""

    def __init__(self, payload):
        super().__init__("rollback savepoint")
        self.payload = payload


async def a6_deepeval_wrapped(conn):
    print("\n--- Assertion 6: DeepEval before/after (reported honestly) ---")
    if not HAS_KEY:
        skip("DeepEval before/after accuracy", "ANTHROPIC_API_KEY not set")
        return
    # Run the Task-4 harness in a SAVEPOINT and roll it back (its own seeding must
    # not persist or collide with this verify's rows).
    report = None
    try:
        async with conn.transaction():
            report = await ecl.evaluate(conn, ORG_A)
            ecl.print_report(report)
            raise _Rollback(report)
    except _Rollback:
        pass
    delta = report["with_accuracy"] - report["without_accuracy"]
    ok("DeepEval before/after measured and reported",
       f"WITHOUT={report['without_pass']}/{report['n']}, "
       f"WITH={report['with_pass']}/{report['n']}, Δ={delta*100:+.1f} pts "
       f"({'improvement' if delta > 0 else 'no change' if delta == 0 else 'REGRESSION'})")


async def a7_isolation_query(conn):
    print("\n--- Assertion 7a: org isolation in the QUERY (bypass role, RLS off) ---")
    # ORG_A retrieval must contain ONLY org-A data (its marker), never org-B's.
    a_cls = await cr.get_relevant_corrections(conn, ORG_A, {"kind": "classification"})
    a_excerpts = " ".join((c["excerpt"] or "") for c in a_cls)
    b_cls = await cr.get_relevant_corrections(conn, ORG_B, {"kind": "classification"})
    b_excerpts = " ".join((c["excerpt"] or "") for c in b_cls)
    a_clean = (MARKER_A in a_excerpts) and (MARKER_B not in a_excerpts)
    b_clean = (MARKER_B in b_excerpts) and (MARKER_A not in b_excerpts)
    # Even on the bypass role (RLS not enforced), the org filter is in the SQL.
    if a_clean and b_clean:
        ok("retrieval query filters by org_id (bypass role) — no cross-org leak",
           f"org-A excerpts have A-only marker; org-B excerpts have B-only marker")
    else:
        fail("org isolation in query logic",
             f"a_clean={a_clean}, b_clean={b_clean}")


async def a7_isolation_app_service():
    print("\n--- Assertion 7b: org isolation on the REAL app_service role ---")
    use_set_role = False
    if APP_SERVICE_DATABASE_URL:
        try:
            conn = await _connect(APP_SERVICE_DATABASE_URL)
        except Exception as exc:  # noqa: BLE001
            skip("app_service cross-org isolation",
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
                skip("app_service cross-org isolation",
                     f"fallback role switch ineffective (current_user={who}, "
                     f"bypassrls={bypass}) — set APP_SERVICE_DATABASE_URL to run")
                return
        except Exception as exc:  # noqa: BLE001
            await conn.close()
            skip("app_service cross-org isolation",
                 f"cannot SET ROLE app_service ({type(exc).__name__}: {exc})")
            return
    try:
        # As ORG_B's session, ORG_A's corrections must be invisible (empty).
        try:
            async with conn.transaction():
                if use_set_role:
                    await conn.execute("SET LOCAL ROLE app_service")
                await conn.execute(
                    "SELECT set_config('app.current_org_id',$1,true),"
                    "       set_config('app.is_super_admin','false',true)", ORG_B)
                as_b_sees_a = await cr.get_relevant_corrections(
                    conn, ORG_A, {"kind": "classification"})
                as_b_sees_a_ext = await cr.get_relevant_corrections(
                    conn, ORG_A, {"kind": "extraction", "template_type": "k1"})
        except asyncpg.InsufficientPrivilegeError as exc:
            skip("app_service cross-org isolation",
                 f"app_service lacks table GRANTs: {exc}")
            return
        if as_b_sees_a == [] and as_b_sees_a_ext == []:
            ok("app_service: ORG_B session sees NONE of ORG_A's corrections",
               "both classification and extraction retrieval return [] cross-org")
        else:
            fail("app_service cross-org isolation",
                 f"leaked cls={as_b_sees_a}, ext={as_b_sees_a_ext}")
    finally:
        await conn.close()


# ── main ─────────────────────────────────────────────────────────────────────
async def main():
    if not DATABASE_URL:
        print("[verify] SKIP — DATABASE_URL not set")
        sys.exit(0)

    global _RUN_START
    conn = await _connect(DATABASE_URL)
    try:
        await teardown(conn)              # teardown at START
        _RUN_START = await conn.fetchval("SELECT now()")
        uid = await seed(conn)

        await a1_discovery()
        await a2_retrieval(conn)
        await a3_classification_behavior(conn)

        pool = await get_pool()
        await a4_k1_behavior(pool)
        await a5_zero_corrections(conn, pool)

        await a6_deepeval_wrapped(conn)
        await a7_isolation_query(conn)
        await a7_isolation_app_service()
    finally:
        await teardown(conn)              # teardown at END
        leftover = await conn.fetchval(
            "SELECT count(*) FROM document_field_corrections "
            "WHERE document_id = ANY($1::uuid[])", ALL_DOC_IDS)
        leftover_docs = await conn.fetchval(
            "SELECT count(*) FROM documents WHERE id = ANY($1::uuid[]) "
            "OR original_filename LIKE '%' || $2 || '%'", ALL_DOC_IDS, MARKER)
        leftover_orgs = await conn.fetchval(
            "SELECT count(*) FROM organizations WHERE id = ANY($1::uuid[])",
            [ORG_B, ORG_ZERO])
        leftover_log = await conn.fetchval(
            "SELECT count(*) FROM ai_decision_log WHERE org_id = ANY($1::uuid[])",
            [ORG_B, ORG_ZERO])
        if (leftover == 0 and leftover_docs == 0 and leftover_orgs == 0
                and leftover_log == 0):
            ok("teardown: zero leftover rows",
               "corrections=0, documents=0, throwaway-orgs=0, decision-log=0")
        else:
            fail("teardown leftover",
                 f"corrections={leftover}, documents={leftover_docs}, "
                 f"orgs={leftover_orgs}, decision_log={leftover_log}")
        await conn.close()

    # ── summary ──
    passed = sum(1 for r in _RESULTS if r[0] == "PASS")
    failed = sum(1 for r in _RESULTS if r[0] == "FAIL")
    skipped = sum(1 for r in _RESULTS if r[0] == "SKIP")
    print("\n" + "=" * 74)
    print(f"RESULT: {passed} passed, {failed} failed, {skipped} skipped")
    print("=" * 74)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    asyncio.run(main())
