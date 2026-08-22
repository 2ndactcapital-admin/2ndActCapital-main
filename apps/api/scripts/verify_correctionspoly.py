"""Corrections-polymorphism verify — document_field_corrections for non-document targets.

Pass/fail only. No interactive prompts (runs UNATTENDED). Idempotent. Teardown at
START and at END, keyed on stable test ids + a marker.

What this sprint changed (schema only, no application code):
  * document_id / org_id are now NULLABLE
  * target_type (NOT NULL, DEFAULT 'document') + target_id (NOT NULL) added
  * two CHECK constraints pin the document<->org pairing and the global-data rule
  * a BEFORE INSERT trigger fills target_id from document_id for document rows,
    so every pre-existing INSERT statement stays valid UNMODIFIED
  * RLS: the org-isolation policy is untouched; a four-policy global shape
    (global read + super-admin insert/update/delete) is added for non-document rows

DSNs / keys:
  DATABASE_URL             — bypass (postgres): seeding, DDL introspection, teardown.
  APP_SERVICE_DATABASE_URL — non-bypass app_service, required for the RLS assertions.
  ANTHROPIC_API_KEY        — required by the DeepEval regression (Task 3). Its
                             absence is a hard FAIL here, NOT a skip: the DeepEval
                             re-run is the load-bearing check of this sprint and a
                             check that passes without running is worse than none.
"""

import asyncio
import glob
import os
import subprocess
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

DATABASE_URL = os.environ.get("DATABASE_URL")
APP_SERVICE_DATABASE_URL = os.environ.get("APP_SERVICE_DATABASE_URL")
HAS_KEY = bool(os.environ.get("ANTHROPIC_API_KEY"))

TABLE = "document_field_corrections"

# ── stable ids / markers ─────────────────────────────────────────────────────
ORG_A = "00000000-0000-0000-0000-000000000001"     # default org (exists)
ORG_B = "0000cf01-0000-0000-0000-0000000000b1"     # throwaway org (cross-org RLS)
MARKER = "correctionspoly_verify_marker"

TEST_AUTH0_SUB = "auth0|test_verify_correctionspoly"
TEST_USER_ID = "99000000-0000-0000-0000-0000000000cf"

DOC_A = "99000000-0000-0000-0000-0000000000c1"     # org-A document target
SEC_GLOBAL_ID = "99000000-0000-0000-0000-0000000000c2"   # throwaway securities_global
NOTE_TERMS_ID = "99000000-0000-0000-0000-0000000000c3"   # throwaway global note terms

# On-record DeepEval figure this sprint must not regress (chancery8, Haiku):
#   WITHOUT retrieval 1/3 = 33.3%   WITH retrieval 3/3 = 100.0%   delta +66.7 pts
DEEPEVAL_ON_RECORD_WITH = 1.0
DEEPEVAL_ON_RECORD_WITHOUT = 1.0 / 3.0
DEEPEVAL_TOLERANCE = 0.01   # "at or near" the on-record 100%

# ── tiny pass/fail harness ──────────────────────────────────────────────────
_RESULTS: list[tuple[str, str, str]] = []


def ok(name, detail=""):
    _RESULTS.append(("PASS", name, detail))
    print(f"[PASS] {name}" + (f" — {detail}" if detail else ""))


def fail(name, detail=""):
    _RESULTS.append(("FAIL", name, detail))
    print(f"[FAIL] {name}" + (f" — {detail}" if detail else ""))


def flag(name, detail=""):
    """Non-failing manual-review flag (still printed and counted separately)."""
    _RESULTS.append(("FLAG", name, detail))
    print(f"[FLAG] {name}" + (f" — {detail}" if detail else ""))


# ── DB helpers ───────────────────────────────────────────────────────────────
async def _connect(dsn):
    return await asyncpg.connect(dsn, statement_cache_size=0, ssl="require")


async def teardown(conn):
    """FK-safe, child-first. Keyed on the stable test ids only."""
    await conn.execute(
        f"DELETE FROM {TABLE} WHERE document_id = $1 OR target_id = ANY($2::uuid[]) "
        f"OR org_id = $3 OR notes = $4",
        DOC_A, [DOC_A, NOTE_TERMS_ID], ORG_B, MARKER)
    await conn.execute(
        "DELETE FROM document_template_extractions WHERE document_id = $1", DOC_A)
    await conn.execute("DELETE FROM documents WHERE id = $1", DOC_A)
    await conn.execute(
        "DELETE FROM portfolio.securities_global_note_terms WHERE id = $1",
        NOTE_TERMS_ID)
    await conn.execute(
        "DELETE FROM portfolio.securities_global WHERE id = $1", SEC_GLOBAL_ID)
    await conn.execute("DELETE FROM users WHERE auth0_sub = $1", TEST_AUTH0_SUB)
    await conn.execute("DELETE FROM organizations WHERE id = $1", ORG_B)


async def seed(conn):
    await conn.execute(
        "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3) "
        "ON CONFLICT (id) DO NOTHING",
        ORG_B, f"CorrectionsPoly Org {MARKER}", f"correctionspoly-{MARKER}")
    await conn.execute(
        """
        INSERT INTO users (id, org_id, email, full_name, auth0_sub, role)
        VALUES ($1, $2, 'verify_correctionspoly@test.local',
                'CorrectionsPoly Verify', $3, 'member')
        ON CONFLICT (auth0_sub) DO NOTHING
        """,
        TEST_USER_ID, ORG_A, TEST_AUTH0_SUB)
    uid = await conn.fetchval(
        "SELECT id FROM users WHERE auth0_sub = $1", TEST_AUTH0_SUB)

    await conn.execute(
        """
        INSERT INTO documents (id, org_id, original_filename, source, status, created_by)
        VALUES ($1, $2, $3, 'upload', 'confirmed', $4)
        ON CONFLICT (id) DO NOTHING
        """,
        DOC_A, ORG_A, f"{MARKER}.pdf", uid)

    # Throwaway GLOBAL structured note + its terms row — the non-document target.
    await conn.execute(
        """
        INSERT INTO portfolio.securities_global
            (id, name, security_type, price_coverage)
        VALUES ($1, $2, 'structured_note', 'no_public_source')
        ON CONFLICT (id) DO NOTHING
        """,
        SEC_GLOBAL_ID, f"CorrectionsPoly Note {MARKER}")
    await conn.execute(
        """
        INSERT INTO portfolio.securities_global_note_terms
            (id, global_security_id, terms_status, product_archetype,
             protection_type, protection_pct)
        VALUES ($1, $2, 'preliminary', 'buffered_note', 'floor', 10)
        ON CONFLICT (id) DO NOTHING
        """,
        NOTE_TERMS_ID, SEC_GLOBAL_ID)
    return uid


# ── assertions ───────────────────────────────────────────────────────────────
async def a1_nullable(conn):
    print("\n--- Assertion 1: document_id and org_id are NULLABLE ---")
    rows = await conn.fetch(
        """
        SELECT column_name, is_nullable
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = $1
          AND column_name IN ('document_id', 'org_id')
        """,
        TABLE)
    got = {r["column_name"]: r["is_nullable"] for r in rows}
    if got.get("document_id") == "YES" and got.get("org_id") == "YES":
        ok("document_id and org_id are nullable", f"{got}")
    else:
        fail("document_id / org_id nullability", f"information_schema says {got}")


async def a2_target_columns(conn):
    print("\n--- Assertion 2: target_type / target_id exist and are NOT NULL ---")
    rows = await conn.fetch(
        """
        SELECT column_name, data_type, is_nullable, column_default
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = $1
          AND column_name IN ('target_type', 'target_id')
        """,
        TABLE)
    got = {r["column_name"]: (r["data_type"], r["is_nullable"]) for r in rows}
    if (got.get("target_type") == ("text", "NO")
            and got.get("target_id") == ("uuid", "NO")):
        ok("target_type (text) + target_id (uuid) exist, both NOT NULL", f"{got}")
    else:
        fail("target_type / target_id shape", f"information_schema says {got}")

    # No FK on target_id — it is deliberately polymorphic; the reason must be
    # documented on the column (sprint requirement).
    fk = await conn.fetchval(
        """
        SELECT count(*) FROM pg_constraint
        WHERE conrelid = $1::regclass AND contype = 'f'
          AND 'target_id' = ANY (
              SELECT attname FROM pg_attribute
              WHERE attrelid = conrelid AND attnum = ANY (conkey))
        """,
        f"public.{TABLE}")
    comment = await conn.fetchval(
        """
        SELECT col_description($1::regclass,
               (SELECT attnum FROM pg_attribute
                WHERE attrelid = $1::regclass AND attname = 'target_id'))
        """,
        f"public.{TABLE}")
    if fk == 0 and comment and "foreign key" in comment.lower():
        ok("target_id has NO foreign key, and the reason is documented in a "
           "COMMENT ON COLUMN", f"{len(comment)} chars")
    else:
        fail("target_id FK / column comment",
             f"fk_count={fk}, comment={comment!r}")


async def a3_backfill(conn):
    print("\n--- Assertion 3: backfill correctness (sample and compare) ---")
    # Every 'document' row must have target_id EQUAL to its document_id — checked
    # by comparing the values, not by counting.
    total = await conn.fetchval(f"SELECT count(*) FROM {TABLE}")
    doc_rows = await conn.fetchval(
        f"SELECT count(*) FROM {TABLE} WHERE target_type = 'document'")
    sample = await conn.fetch(
        f"SELECT id, document_id, target_id, target_type FROM {TABLE} "
        f"WHERE target_type = 'document' ORDER BY corrected_at LIMIT 50")
    mismatched = [dict(r) for r in sample if r["target_id"] != r["document_id"]]
    bad_type = await conn.fetchval(
        f"SELECT count(*) FROM {TABLE} "
        f"WHERE document_id IS NOT NULL AND target_type <> 'document'")

    if mismatched or bad_type:
        fail("backfill correctness",
             f"mismatched sample rows={mismatched}, "
             f"document-bearing rows typed non-document={bad_type}")
        return
    if total == 0:
        # Honest reporting: the table was EMPTY when the migration ran, so the
        # backfill UPDATE touched 0 rows. The mechanism is still proven by
        # assertion 4 (legacy INSERT shape -> target_type='document',
        # target_id=document_id), which is the same rule the backfill applied.
        ok("backfill correctness (vacuously true — table has 0 rows)",
           "the migration's backfill UPDATE covered 0 existing rows; the "
           "'document' rule itself is proven by assertion 4")
    else:
        ok("every 'document' row has target_id = document_id",
           f"{len(sample)} of {doc_rows} document rows sampled and compared, "
           f"0 mismatches; total rows={total}")


async def a4_legacy_insert_shape(conn, uid):
    print("\n--- Assertion 4: the EXISTING call sites' INSERT statements still "
          "work UNMODIFIED ---")
    # These are the literal INSERT statements from, respectively:
    #   services/document_review.py::submit_field_correction
    #   services/document_review.py::submit_classification_correction
    #   scripts/eval_correction_loop.py::_seed_correction
    # None of them mention target_type or target_id. All must still succeed and
    # must land as ('document', document_id).
    tmpl_id = await conn.fetchval(
        """
        INSERT INTO document_template_extractions
            (document_id, org_id, template_type, extraction_source, mapped_fields)
        VALUES ($1, $2, 'k1', 'native', '{"ordinary_business_income": "1234.00"}'::jsonb)
        RETURNING id
        """,
        DOC_A, ORG_A)

    legacy_ids = []
    try:
        legacy_ids.append(await conn.fetchval(
            f"""
            INSERT INTO {TABLE}
                (document_id, org_id, template_extraction_id, field_name,
                 original_value, corrected_value, notes, corrected_by)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
            RETURNING id
            """,
            DOC_A, ORG_A, tmpl_id, "ordinary_business_income",
            "1234.00", "5678.90", MARKER, uid))
        legacy_ids.append(await conn.fetchval(
            f"""
            INSERT INTO {TABLE}
                (document_id, org_id, template_extraction_id, field_name,
                 original_value, corrected_value, notes, corrected_by)
            VALUES ($1, $2, NULL, $3, $4, $5, $6, $7)
            RETURNING id
            """,
            DOC_A, ORG_A, cr.CLASSIFICATION_FIELD, "will", "estate_plan",
            MARKER, uid))
        legacy_ids.append(await conn.fetchval(
            f"""
            INSERT INTO {TABLE}
                (document_id, org_id, template_extraction_id, field_name,
                 original_value, corrected_value)
            VALUES ($1, $2, NULL, $3, $4, $5)
            RETURNING id
            """,
            DOC_A, ORG_A, cr.CLASSIFICATION_FIELD, "subscription_doc",
            "accreditation"))
    except Exception as exc:  # noqa: BLE001
        fail("existing call-site INSERT statements still valid",
             f"{type(exc).__name__}: {exc}")
        return None, tmpl_id

    rows = await conn.fetch(
        f"SELECT id, target_type, target_id, document_id FROM {TABLE} "
        f"WHERE id = ANY($1::uuid[])", legacy_ids)
    wrong = [dict(r) for r in rows
             if r["target_type"] != "document" or r["target_id"] != r["document_id"]]
    if len(rows) == 3 and not wrong:
        ok("all 3 pre-existing INSERT shapes succeed unmodified and land as "
           "target_type='document', target_id=document_id",
           "submit_field_correction / submit_classification_correction / "
           "eval_correction_loop._seed_correction")
    else:
        fail("legacy INSERT shape result", f"rows={len(rows)}, wrong={wrong}")

    # And the real retrieval path still reads them back (the SQL in
    # correction_retrieval is unchanged; this proves the schema change did not
    # break its joins or its org_id filter).
    cls = await cr.get_relevant_corrections(conn, ORG_A, {"kind": "classification"})
    ext = await cr.get_relevant_corrections(
        conn, ORG_A, {"kind": "extraction", "template_type": "k1"})
    cls_hit = any(c["corrected_value"] == "estate_plan" for c in cls)
    ext_hit = any(c["field_name"] == "ordinary_business_income"
                  and c["corrected_value"] == "5678.90" for c in ext)
    if cls_hit and ext_hit:
        ok("correction_retrieval.get_relevant_corrections still returns both "
           "shapes post-migration",
           f"classification hits={len(cls)}, extraction hits={len(ext)} "
           "(structural check — NOT a substitute for the DeepEval measurement)")
    else:
        fail("get_relevant_corrections post-migration",
             f"cls_hit={cls_hit} ({cls}), ext_hit={ext_hit} ({ext})")
    return legacy_ids, tmpl_id


async def _expect_rejection(conn, label, sql, *params):
    """Run an INSERT that MUST be rejected. Returns True when it was."""
    try:
        async with conn.transaction():
            await conn.execute(sql, *params)
    except (asyncpg.CheckViolationError, asyncpg.NotNullViolationError) as exc:
        ok(label, f"rejected by {exc.constraint_name or type(exc).__name__}")
        return True
    except Exception as exc:  # noqa: BLE001
        fail(label, f"rejected, but by the WRONG error: {type(exc).__name__}: {exc}")
        return False
    fail(label, "INSERT was ACCEPTED — the CHECK constraint did not fire")
    return False


async def a5_check_document_pairing(conn, uid):
    print("\n--- Assertion 5: CHECK rejects a 'document' row missing document_id "
          "or org_id ---")
    base = (f"INSERT INTO {TABLE} (document_id, org_id, target_type, target_id, "
            f"field_name, original_value, corrected_value, notes, corrected_by) "
            f"VALUES ($1, $2, 'document', $3, 'buffer_pct', 'a', 'b', $4, $5)")
    await _expect_rejection(
        conn, "target_type='document' with NULL document_id is rejected",
        base, None, ORG_A, DOC_A, MARKER, uid)
    await _expect_rejection(
        conn, "target_type='document' with NULL org_id is rejected",
        base, DOC_A, None, DOC_A, MARKER, uid)


async def a6_check_global_no_org(conn, uid):
    print("\n--- Assertion 6: CHECK rejects a 'note_terms' row that CARRIES an "
          "org_id ---")
    await _expect_rejection(
        conn, "target_type='note_terms' with a non-NULL org_id is rejected",
        f"INSERT INTO {TABLE} (document_id, org_id, target_type, target_id, "
        f"field_name, original_value, corrected_value, notes, corrected_by) "
        f"VALUES (NULL, $1, 'note_terms', $2, 'protection_type', 'floor', "
        f"'buffer', $3, $4)",
        ORG_A, NOTE_TERMS_ID, MARKER, uid)
    # And the discriminator itself is closed.
    await _expect_rejection(
        conn, "an unknown target_type is rejected",
        f"INSERT INTO {TABLE} (document_id, org_id, target_type, target_id, "
        f"field_name, original_value, corrected_value, notes) "
        f"VALUES (NULL, NULL, 'not_a_real_target', $1, 'f', 'a', 'b', $2)",
        NOTE_TERMS_ID, MARKER)


async def a7_insert_note_terms(conn, uid):
    print("\n--- Assertion 7: a REAL note_terms correction row inserts "
          "(org_id NULL, target_id -> securities_global_note_terms.id) ---")
    # The motivating case from the sprint prompt: a buffer misread as a floor.
    try:
        corr_id = await conn.fetchval(
            f"""
            INSERT INTO {TABLE}
                (document_id, org_id, target_type, target_id, template_extraction_id,
                 field_name, original_value, corrected_value, notes, corrected_by)
            VALUES (NULL, NULL, 'note_terms', $1, NULL,
                    'protection_type', 'floor', 'buffer', $2, $3)
            RETURNING id
            """,
            NOTE_TERMS_ID, MARKER, uid)
    except Exception as exc:  # noqa: BLE001
        fail("note_terms correction insert", f"{type(exc).__name__}: {exc}")
        return None

    row = await conn.fetchrow(
        f"SELECT c.org_id, c.document_id, c.target_type, c.target_id, "
        f"       c.field_name, c.original_value, c.corrected_value, t.id AS terms_id "
        f"FROM {TABLE} c "
        f"LEFT JOIN portfolio.securities_global_note_terms t ON t.id = c.target_id "
        f"WHERE c.id = $1", corr_id)
    if (row and row["org_id"] is None and row["document_id"] is None
            and row["target_type"] == "note_terms"
            and row["terms_id"] is not None):
        ok("note_terms correction row inserted with org_id NULL and target_id "
           "resolving to a real securities_global_note_terms row",
           f"{row['field_name']}: {row['original_value']} -> "
           f"{row['corrected_value']}")
    else:
        fail("note_terms correction row shape", f"{dict(row) if row else None}")

    # Non-regression on the document retrieval path: the org-scoped queries must
    # NOT pick this global row up (they filter c.org_id = $1, and org_id is NULL).
    cls = await cr.get_relevant_corrections(conn, ORG_A, {"kind": "classification"})
    leaked = [c for c in cls if c["corrected_value"] == "buffer"]
    if not leaked:
        ok("the global note_terms row never surfaces in the org-scoped document "
           "retrieval path", "org_id IS NULL never satisfies c.org_id = $1")
    else:
        fail("global row leaked into document retrieval", f"{leaked}")
    return corr_id


async def a8_rls(conn_bypass, note_corr_id, uid):
    print("\n--- Assertion 8: RLS under the REAL app_service role ---")
    if not APP_SERVICE_DATABASE_URL:
        fail("RLS under app_service",
             "APP_SERVICE_DATABASE_URL not set — the RLS assertions cannot run "
             "on a non-bypass role, so they are NOT counted as passing")
        return

    # A document-target correction owned by ORG_B, used for the cross-org check.
    doc_b = await conn_bypass.fetchval(
        """
        INSERT INTO documents (org_id, original_filename, source, status)
        VALUES ($1, $2, 'upload', 'confirmed') RETURNING id
        """,
        ORG_B, f"{MARKER}_orgb.pdf")
    b_corr_id = await conn_bypass.fetchval(
        f"""
        INSERT INTO {TABLE}
            (document_id, org_id, field_name, original_value, corrected_value, notes)
        VALUES ($1, $2, $3, 'will', 'estate_plan', $4)
        RETURNING id
        """,
        doc_b, ORG_B, cr.CLASSIFICATION_FIELD, MARKER)

    try:
        conn = await _connect(APP_SERVICE_DATABASE_URL)
    except Exception as exc:  # noqa: BLE001
        fail("RLS under app_service", f"could not connect: {type(exc).__name__}: {exc}")
        return
    try:
        who = await conn.fetchval("SELECT current_user")
        bypass = await conn.fetchval(
            "SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user")
        if bypass:
            fail("RLS under app_service",
                 f"connected as {who} which BYPASSES RLS — the check would be "
                 "meaningless")
            return

        # 8a — global read: NO org context at all.
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('app.current_org_id', '', true),"
                "       set_config('app.is_super_admin', 'false', true)")
            seen = await conn.fetchval(
                f"SELECT count(*) FROM {TABLE} WHERE id = $1", note_corr_id)
        if seen == 1:
            ok("note_terms correction is READABLE under app_service with NO org "
               "context set (global read)", f"current_user={who}")
        else:
            fail("global read of the note_terms correction",
                 f"count={seen} under app_service with no org context")

        # 8b — THE critical non-regression check: a document-target correction
        # belonging to ORG_B must stay invisible to an ORG_A session.
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('app.current_org_id', $1, true),"
                "       set_config('app.is_super_admin', 'false', true)", ORG_A)
            cross = await conn.fetchval(
                f"SELECT count(*) FROM {TABLE} WHERE id = $1", b_corr_id)
            own = await conn.fetchval(
                f"SELECT count(*) FROM {TABLE} "
                f"WHERE org_id = $1 AND target_type = 'document'", ORG_A)
            global_visible = await conn.fetchval(
                f"SELECT count(*) FROM {TABLE} WHERE id = $1", note_corr_id)
        if cross == 0 and own > 0 and global_visible == 1:
            ok("cross-org invisibility of target_type='document' rows is INTACT",
               f"ORG_A session sees 0 of ORG_B's document corrections, "
               f"{own} of its own, and the global note_terms row")
        else:
            fail("cross-org invisibility (NON-REGRESSION)",
                 f"org_b_rows_visible_to_org_a={cross} (must be 0), "
                 f"own_rows={own} (must be >0), global={global_visible}")

        # 8c — the org-scoped retrieval service itself, on the non-bypass role.
        async with conn.transaction():
            await conn.execute(
                "SELECT set_config('app.current_org_id', $1, true),"
                "       set_config('app.is_super_admin', 'false', true)", ORG_B)
            as_b_sees_a = await cr.get_relevant_corrections(
                conn, ORG_A, {"kind": "classification"})
        if as_b_sees_a == []:
            ok("get_relevant_corrections leaks nothing cross-org on app_service",
               "ORG_B session asking for ORG_A returns []")
        else:
            fail("cross-org retrieval leak", f"{as_b_sees_a}")
    finally:
        await conn.close()
        await conn_bypass.execute(
            f"DELETE FROM {TABLE} WHERE id = $1", b_corr_id)
        await conn_bypass.execute("DELETE FROM documents WHERE id = $1", doc_b)


async def a9_deepeval(conn):
    print("\n--- Assertion 9: DEEPEVAL REGRESSION (Task 3) ---")
    print("    on record (chancery8, Haiku): WITHOUT 1/3 = 33.3% -> "
          "WITH 3/3 = 100.0%  (delta +66.7 pts)")
    if not HAS_KEY:
        fail("DeepEval regression re-run (scripts/eval_correction_loop.py)",
             "ANTHROPIC_API_KEY is not set in this environment, so the "
             "measurement CANNOT be re-run. Reported as FAIL, not SKIP: this is "
             "the load-bearing check of the sprint and a check that passes "
             "without running is worse than no check. Set ANTHROPIC_API_KEY and "
             "re-run to clear this gate.")
        return

    import eval_correction_loop as ecl
    tr = conn.transaction()
    await tr.start()
    try:
        report = await ecl.evaluate(conn, ecl.DEFAULT_ORG)
        ecl.print_report(report)
    finally:
        await tr.rollback()

    wo = report["without_accuracy"]
    w = report["with_accuracy"]
    detail = (f"re-run: WITHOUT {report['without_pass']}/{report['n']} = "
              f"{wo * 100:.1f}%  ->  WITH {report['with_pass']}/{report['n']} = "
              f"{w * 100:.1f}%  (delta {(w - wo) * 100:+.1f} pts); "
              f"on record 33.3% -> 100.0% (+66.7 pts)")
    if w >= DEEPEVAL_ON_RECORD_WITH - DEEPEVAL_TOLERANCE:
        ok("DeepEval regression — WITH-retrieval accuracy is at the on-record "
           "100% figure", detail)
    else:
        fail("DeepEval regression — WITH-retrieval accuracy COLLAPSED", detail)
    if wo <= DEEPEVAL_ON_RECORD_WITHOUT + DEEPEVAL_TOLERANCE:
        ok("DeepEval baseline WITHOUT-retrieval still at/below the on-record "
           "33.3%", detail)
    else:
        flag("DeepEval baseline drifted upward",
             detail + " — the delta shrank because the BASE model improved, not "
                      "because retrieval regressed; manual review")


def a10_call_sites_untouched():
    print("\n--- Assertion 10: no existing call site was modified (scope creep "
          "check) ---")
    call_site_files = [
        "apps/api/services/correction_retrieval.py",
        "apps/api/services/document_review.py",
        "apps/api/routers/document_review.py",
        "apps/api/services/document_classifier.py",
        "apps/api/services/textract_extraction.py",
        "apps/api/scripts/eval_correction_loop.py",
        "apps/api/scripts/verify_chancery6.py",
        "apps/api/scripts/verify_chancery8.py",
    ]
    try:
        out = subprocess.run(
            ["git", "diff", "--name-only", "origin/main...HEAD", "--"]
            + call_site_files,
            cwd=_REPO_ROOT, capture_output=True, text=True, timeout=60)
        working = subprocess.run(
            ["git", "status", "--porcelain", "--"] + call_site_files,
            cwd=_REPO_ROOT, capture_output=True, text=True, timeout=60)
    except Exception as exc:  # noqa: BLE001
        flag("call-site scope-creep check", f"git unavailable: {exc}")
        return
    touched = sorted(set(
        [p for p in out.stdout.split() if p]
        + [ln.split()[-1] for ln in working.stdout.splitlines() if ln.strip()]))
    if not touched:
        ok("every existing call site is byte-for-byte unmodified",
           f"{len(call_site_files)} files checked vs origin/main and the working "
           "tree; the schema change is backward-compatible via the column DEFAULT "
           "+ BEFORE INSERT trigger")
    else:
        flag("call sites were MODIFIED — scope creep, flag for manual review",
             ", ".join(touched))


# ── main ─────────────────────────────────────────────────────────────────────
async def main():
    if not DATABASE_URL:
        print("[verify] SKIP — DATABASE_URL not set")
        sys.exit(0)

    conn = await _connect(DATABASE_URL)
    try:
        await teardown(conn)              # teardown at START
        uid = await seed(conn)

        await a1_nullable(conn)
        await a2_target_columns(conn)
        await a3_backfill(conn)
        await a4_legacy_insert_shape(conn, uid)
        await a5_check_document_pairing(conn, uid)
        await a6_check_global_no_org(conn, uid)
        note_corr_id = await a7_insert_note_terms(conn, uid)
        await a8_rls(conn, note_corr_id, uid)
        await a9_deepeval(conn)
        a10_call_sites_untouched()
    finally:
        await teardown(conn)              # teardown at END
        leftover = await conn.fetchval(
            f"SELECT count(*) FROM {TABLE} WHERE notes = $1 "
            f"OR document_id = $2 OR target_id = ANY($3::uuid[])",
            MARKER, DOC_A, [DOC_A, NOTE_TERMS_ID])
        leftover_terms = await conn.fetchval(
            "SELECT count(*) FROM portfolio.securities_global_note_terms "
            "WHERE id = $1", NOTE_TERMS_ID)
        leftover_org = await conn.fetchval(
            "SELECT count(*) FROM organizations WHERE id = $1", ORG_B)
        if leftover == 0 and leftover_terms == 0 and leftover_org == 0:
            ok("teardown: zero leftover rows")
        else:
            fail("teardown leftovers",
                 f"corrections={leftover}, note_terms={leftover_terms}, "
                 f"orgs={leftover_org}")
        await conn.close()

    passed = sum(1 for s, _, _ in _RESULTS if s == "PASS")
    failed = sum(1 for s, _, _ in _RESULTS if s == "FAIL")
    flagged = sum(1 for s, _, _ in _RESULTS if s == "FLAG")
    print("\n" + "=" * 74)
    print(f"verify_correctionspoly: {passed} passed, {failed} failed, "
          f"{flagged} flagged")
    for status, name, detail in _RESULTS:
        if status in ("FAIL", "FLAG"):
            print(f"  [{status}] {name} — {detail}")
    print("=" * 74)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    asyncio.run(main())
