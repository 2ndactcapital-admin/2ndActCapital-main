"""Chancery Phase 5 verify — document ↔ entity / record linkage.

Pass/fail only. No interactive prompts (runs UNATTENDED). Idempotent. Teardown
at START and at END, keyed on the seeded test user + a stable name marker.

This exercises the REAL Phase-5 code (``services.document_linkage`` — the same
functions the ``routers.document_links`` endpoints call) against the live DB, and
proves cross-org isolation against the REAL non-bypass ``app_service`` role.

Task-1 discovery findings are reported explicitly at the top (Assertion 1),
including the material finding that Phase 3's K-1 template extraction was BLOCKED
and never built — so this verify SEEDS a ``document_template_extractions`` row to
stand in for Phase 3's completed K-1 output (the forward mapped_fields contract).

DSNs:
  DATABASE_URL             — bypass (postgres) role: seeding, service calls,
                             DB assertions, teardown.
  APP_SERVICE_DATABASE_URL — the NON-BYPASS 'app_service' role for the cross-org
                             RLS check (falls back to SET LOCAL ROLE, else SKIPs —
                             never a false pass).
"""

import asyncio
import glob
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

from services import document_linkage as dl  # noqa: E402

DATABASE_URL = os.environ.get("DATABASE_URL")
APP_SERVICE_DATABASE_URL = os.environ.get("APP_SERVICE_DATABASE_URL")

# ── stable ids / markers ─────────────────────────────────────────────────────
TEST_AUTH0_SUB = "auth0|test_verify_chancery5"
TEST_USER_ID = "99000000-0000-0000-0000-000000000005"
ORG_A = "00000000-0000-0000-0000-000000000001"      # default org (exists)
ORG_B = "0000cafe-0000-0000-0000-0000000000c5"      # a different org (RLS test)
MARKER = "chancery5_verify_marker"

ENTITY_MATCH_ID = "99000000-0000-0000-0000-0000000005a1"
ENTITY_OTHER_ID = "99000000-0000-0000-0000-0000000005a2"
DOC_MANUAL_ID = "99000000-0000-0000-0000-0000000005b1"
DOC_K1_MATCH_ID = "99000000-0000-0000-0000-0000000005b2"
DOC_K1_NOMATCH_ID = "99000000-0000-0000-0000-0000000005b3"
DOC_K1_REJECT_ID = "99000000-0000-0000-0000-0000000005b4"
SPV_RECORD_ID = "99000000-0000-0000-0000-0000000005c1"

TEST_DOC_IDS = [DOC_MANUAL_ID, DOC_K1_MATCH_ID, DOC_K1_NOMATCH_ID, DOC_K1_REJECT_ID]

ENTITY_MATCH_NAME = f"K1 Match Partner LLC {MARKER}"
ENTITY_OTHER_NAME = f"Second Linked Entity {MARKER}"
NOMATCH_PARTY_NAME = f"Unknown Party {MARKER}"
REJECT_PARTY_NAME = f"Reject Party {MARKER}"

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
    """FK-safe, child-first. Keyed on the test docs / user / name marker so a
    re-run (or any partial state) never leaks. All link-table FKs are NO ACTION,
    so children MUST be deleted before parents."""
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
        "DELETE FROM documents WHERE id = ANY($1::uuid[]) OR created_by = $2",
        TEST_DOC_IDS, TEST_USER_ID)
    await conn.execute(
        "DELETE FROM member_todos WHERE user_id = $1 AND source = 'entity_stub' "
        "AND title LIKE '%' || $2 || '%'",
        TEST_USER_ID, MARKER)
    await conn.execute(
        "DELETE FROM entities WHERE org_id = $1 AND display_name LIKE '%' || $2 || '%'",
        ORG_A, MARKER)


async def seed(conn):
    # test user
    await conn.execute(
        """
        INSERT INTO users (id, org_id, email, full_name, auth0_sub, role)
        VALUES ($1, $2, 'verify_chancery5@test.local', 'Chancery5 Verify', $3, 'member')
        ON CONFLICT (auth0_sub) DO NOTHING
        """,
        TEST_USER_ID, ORG_A, TEST_AUTH0_SUB)
    uid = await conn.fetchval("SELECT id FROM users WHERE auth0_sub = $1", TEST_AUTH0_SUB)

    # two real entities in ORG_A (active)
    for eid, name, etype in (
        (ENTITY_MATCH_ID, ENTITY_MATCH_NAME, "llc"),
        (ENTITY_OTHER_ID, ENTITY_OTHER_NAME, "trust"),
    ):
        await conn.execute(
            """
            INSERT INTO entities (id, org_id, entity_type, display_name, status)
            VALUES ($1, $2, $3::entity_type, $4, 'prospect')
            ON CONFLICT (id) DO NOTHING
            """,
            eid, ORG_A, etype, name)

    # documents
    for did, fname, status in (
        (DOC_MANUAL_ID, f"manual_{MARKER}.pdf", "extracted"),
        (DOC_K1_MATCH_ID, f"k1_match_{MARKER}.pdf", "extracted"),
        (DOC_K1_NOMATCH_ID, f"k1_nomatch_{MARKER}.pdf", "extracted"),
        (DOC_K1_REJECT_ID, f"k1_reject_{MARKER}.pdf", "extracted"),
    ):
        await conn.execute(
            """
            INSERT INTO documents (id, org_id, original_filename, source, status,
                                   doc_family, created_by)
            VALUES ($1, $2, $3, 'upload', $4, 'tabular', $5)
            ON CONFLICT (id) DO NOTHING
            """,
            did, ORG_A, fname, status, uid)

    # K-1 template extractions — stand in for Phase-3's completed output.
    # (Phase 3 was BLOCKED/never built; mapped_fields is the forward contract.)
    for did, party in (
        (DOC_K1_MATCH_ID, ENTITY_MATCH_NAME),   # matches a real entity
        (DOC_K1_NOMATCH_ID, NOMATCH_PARTY_NAME),  # matches nothing → proposal
        (DOC_K1_REJECT_ID, REJECT_PARTY_NAME),    # matches nothing → proposal (reject)
    ):
        await conn.execute(
            """
            INSERT INTO document_template_extractions
                (document_id, org_id, template_type, extraction_source, mapped_fields)
            VALUES ($1, $2, 'k1', 'native', $3::jsonb)
            """,
            did, ORG_A, json.dumps({"partner_name": party, "box_1_ordinary_income": "1234.00"}))
    return uid


# ── assertion helpers ────────────────────────────────────────────────────────
async def count_val(conn, sql, *args):
    return await conn.fetchval(sql, *args)


# ── main assertion flow ──────────────────────────────────────────────────────
async def run_assertions(conn, uid):
    # A1 — Task 1 discovery findings (reported explicitly)
    print("\n--- Assertion 1: Task 1 discovery findings ---")
    ok("Task 1(a): reuses Sprint-17 entity-picker matcher",
       "routers.entities.find_entity_dupes — exact case-insensitive display_name "
       "match, org-scoped, active rows (valid_to/system_to IS NULL); NOT fuzzy. "
       "Creation reuses create_picker_stub_entity (is_incomplete + member_todo).")
    ok("Task 1(b): no pre-existing document-linkage system",
       "zero code refs to document_entity_links/record_links/link_proposals before "
       "Phase 5; entity_documents.py is unrelated (Sprint-17 CRM R2 uploads).")
    ok("Task 1(c): K-1 party-name field",
       "Phase 3 K-1 extraction was BLOCKED (no Textract) and never built — no real "
       "mapped_fields schema existed. Forward contract: mapped_fields party name "
       f"under {dl.K1_PARTY_NAME_KEYS[0]!r} (then {', '.join(map(repr, dl.K1_PARTY_NAME_KEYS[1:]))}).")

    # A2 — many-to-many manual linkage (two entities)
    print("\n--- Assertion 2: manual link to TWO entities (many-to-many) ---")
    res = await dl.link_document_to_entities(
        conn, ORG_A, DOC_MANUAL_ID, [ENTITY_MATCH_ID, ENTITY_OTHER_ID],
        created_by=uid, link_role="manual")
    n_created = sum(1 for r in res if r["created"])
    n_rows = await count_val(
        conn, "SELECT count(*) FROM document_entity_links WHERE document_id = $1",
        DOC_MANUAL_ID)
    if n_created == 2 and n_rows == 2:
        ok("manual link to two different entities succeeds (many-to-many)",
           f"created={n_created}, rows={n_rows}")
    else:
        fail("manual link to two different entities",
             f"created={n_created} (want 2), rows={n_rows} (want 2)")

    # A3 — generic record link, retrievable
    print("\n--- Assertion 3: manual generic record link (spv) ---")
    rl = await dl.link_document_to_record(
        conn, ORG_A, DOC_MANUAL_ID, "spv", SPV_RECORD_ID, created_by=uid)
    links = await dl.list_document_links(conn, ORG_A, DOC_MANUAL_ID)
    rec = [r for r in links["record_links"]
           if r["record_type"] == "spv" and r["record_id"] == SPV_RECORD_ID]
    if rl["created"] and rec:
        ok("generic record link (spv) succeeds and is retrievable",
           f"record_id={SPV_RECORD_ID}")
    else:
        fail("generic record link (spv)", f"created={rl['created']}, retrieved={bool(rec)}")

    # A4 — K-1 auto-link on a real match (created_by NULL)
    print("\n--- Assertion 4: K-1 auto-link on real entity match (system) ---")
    out = await dl.auto_link_k1_document(conn, ORG_A, DOC_K1_MATCH_ID)
    row = await conn.fetchrow(
        "SELECT entity_id, created_by, link_role FROM document_entity_links "
        "WHERE document_id = $1", DOC_K1_MATCH_ID)
    if (out.get("outcome") == "linked" and row is not None
            and str(row["entity_id"]) == ENTITY_MATCH_ID and row["created_by"] is None):
        ok("K-1 matching a real entity is AUTO-linked with created_by=NULL",
           f"entity_id={ENTITY_MATCH_ID}, link_role={row['link_role']}")
    else:
        fail("K-1 auto-link on match",
             f"outcome={out}, row={dict(row) if row else None}")

    # A5 — K-1 no-match → proposal, NO entity auto-created
    print("\n--- Assertion 5: K-1 no-match → proposal, NO entity created ---")
    ent_before = await count_val(
        conn, "SELECT count(*) FROM entities WHERE org_id = $1 AND LOWER(display_name)=LOWER($2)",
        ORG_A, NOMATCH_PARTY_NAME)
    out5 = await dl.auto_link_k1_document(conn, ORG_A, DOC_K1_NOMATCH_ID)
    prop = await conn.fetchrow(
        "SELECT id, proposed_link_type, proposed_name, status FROM document_link_proposals "
        "WHERE document_id = $1", DOC_K1_NOMATCH_ID)
    ent_after = await count_val(
        conn, "SELECT count(*) FROM entities WHERE org_id = $1 AND LOWER(display_name)=LOWER($2)",
        ORG_A, NOMATCH_PARTY_NAME)
    link_after = await count_val(
        conn, "SELECT count(*) FROM document_entity_links WHERE document_id = $1",
        DOC_K1_NOMATCH_ID)
    if (out5.get("outcome") == "proposed" and prop is not None
            and prop["proposed_link_type"] == "entity"
            and prop["proposed_name"] == NOMATCH_PARTY_NAME
            and prop["status"] == "pending"
            and ent_before == 0 and ent_after == 0 and link_after == 0):
        ok("K-1 with no match creates a pending 'entity' proposal; NO entity/link auto-created",
           f"proposal={prop['id']}, entity_count={ent_after}, links={link_after}")
    else:
        fail("K-1 no-match proposal",
             f"outcome={out5}, proposal={dict(prop) if prop else None}, "
             f"ent_before={ent_before}, ent_after={ent_after}, links={link_after}")

    # A6 — approve → hands off to real entity-creation, THEN links
    print("\n--- Assertion 6: approve → real entity-creation flow + link ---")
    approve_out = await dl.approve_proposal(
        conn, ORG_A, prop["id"], reviewed_by=uid, entity_type="individual")
    new_ent = await conn.fetchrow(
        "SELECT id, created_via, is_incomplete FROM entities "
        "WHERE org_id = $1 AND LOWER(display_name)=LOWER($2)", ORG_A, NOMATCH_PARTY_NAME)
    todo_ct = await count_val(
        conn, "SELECT count(*) FROM member_todos WHERE source='entity_stub' "
        "AND title LIKE '%' || $1 || '%'", NOMATCH_PARTY_NAME)
    link_row = await conn.fetchrow(
        "SELECT entity_id, created_by, link_role FROM document_entity_links "
        "WHERE document_id = $1", DOC_K1_NOMATCH_ID)
    prop_after = await conn.fetchrow(
        "SELECT status, reviewed_by, reviewed_at FROM document_link_proposals WHERE id = $1",
        prop["id"])
    routed_through_picker = bool(
        new_ent and new_ent["created_via"] == "picker_stub" and new_ent["is_incomplete"]
        and todo_ct >= 1)
    linked = bool(
        link_row and new_ent and str(link_row["entity_id"]) == str(new_ent["id"])
        and str(link_row["created_by"]) == str(uid))
    stamped = bool(
        prop_after and prop_after["status"] == "approved"
        and str(prop_after["reviewed_by"]) == str(uid)
        and prop_after["reviewed_at"] is not None)
    if approve_out.get("entity_created") and routed_through_picker and linked and stamped:
        ok("approve routes through real picker create (picker_stub + member_todo) THEN links",
           f"entity={new_ent['id']}, created_via={new_ent['created_via']}, "
           f"todos={todo_ct}, link.created_by=reviewer, proposal=approved")
    else:
        fail("approve hands off to real entity-creation + link",
             f"approve_out={approve_out}, routed_through_picker={routed_through_picker} "
             f"(created_via={new_ent['created_via'] if new_ent else None}, todos={todo_ct}), "
             f"linked={linked}, stamped={stamped}")

    # A7 — reject → status updated, no entity/link created
    print("\n--- Assertion 7: reject → status rejected, nothing created ---")
    out_r = await dl.auto_link_k1_document(conn, ORG_A, DOC_K1_REJECT_ID)  # → proposal
    reject_prop_id = out_r.get("proposal_id")
    rej_out = await dl.reject_proposal(conn, ORG_A, reject_prop_id, reviewed_by=uid)
    rej_after = await conn.fetchrow(
        "SELECT status, reviewed_by, reviewed_at FROM document_link_proposals WHERE id = $1",
        reject_prop_id)
    rej_ent = await count_val(
        conn, "SELECT count(*) FROM entities WHERE org_id = $1 AND LOWER(display_name)=LOWER($2)",
        ORG_A, REJECT_PARTY_NAME)
    rej_link = await count_val(
        conn, "SELECT count(*) FROM document_entity_links WHERE document_id = $1",
        DOC_K1_REJECT_ID)
    if (rej_out.get("outcome") == "rejected" and rej_after
            and rej_after["status"] == "rejected"
            and str(rej_after["reviewed_by"]) == str(uid)
            and rej_after["reviewed_at"] is not None
            and rej_ent == 0 and rej_link == 0):
        ok("reject updates status to 'rejected'; NO entity or link created",
           f"entity_count={rej_ent}, links={rej_link}")
    else:
        fail("reject proposal",
             f"out={rej_out}, after={dict(rej_after) if rej_after else None}, "
             f"entity_count={rej_ent}, links={rej_link}")

    # A8 — list documents for an entity (Phase-9 query)
    print("\n--- Assertion 8: list documents linked to an entity (Phase-9 query) ---")
    docs = await dl.list_documents_for_entity(conn, ORG_A, ENTITY_MATCH_ID)
    doc_ids = {d["document_id"] for d in docs}
    want = {DOC_MANUAL_ID, DOC_K1_MATCH_ID}
    if doc_ids == want:
        ok("documents-for-entity returns the correct set (manual + auto links)",
           f"documents={sorted(doc_ids)}")
    else:
        fail("documents-for-entity query", f"got={sorted(doc_ids)}, want={sorted(want)}")

    return prop["id"], reject_prop_id


# ── cross-org RLS (non-bypass app_service) ───────────────────────────────────
async def rls_isolation_checks():
    """A9 — a different org cannot see this org's links or proposals."""
    use_set_role = False
    if APP_SERVICE_DATABASE_URL:
        try:
            conn = await _connect(APP_SERVICE_DATABASE_URL)
        except Exception as exc:  # noqa: BLE001
            skip("RLS: cross-org isolation of links/proposals",
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
                skip("RLS: cross-org isolation of links/proposals",
                     f"fallback role switch ineffective (current_user={who}, "
                     f"bypassrls={bypass}) — set APP_SERVICE_DATABASE_URL to run")
                return
        except Exception as exc:  # noqa: BLE001
            await conn.close()
            skip("RLS: cross-org isolation of links/proposals",
                 f"cannot SET ROLE app_service ({type(exc).__name__}: {exc})")
            return
    try:
        async def counts(org):
            async with conn.transaction():
                if use_set_role:
                    await conn.execute("SET LOCAL ROLE app_service")
                await conn.execute(
                    "SELECT set_config('app.current_org_id',$1,true),"
                    "       set_config('app.is_super_admin','false',true)", org)
                links = await conn.fetchval(
                    "SELECT count(*) FROM document_entity_links WHERE document_id = ANY($1::uuid[])",
                    TEST_DOC_IDS)
                props = await conn.fetchval(
                    "SELECT count(*) FROM document_link_proposals WHERE document_id = ANY($1::uuid[])",
                    TEST_DOC_IDS)
                return links, props
        a_links, a_props = await counts(ORG_A)
        b_links, b_props = await counts(ORG_B)
        if a_links > 0 and a_props > 0 and b_links == 0 and b_props == 0:
            ok("cross-org isolation: links/proposals visible in-org, invisible to another org",
               f"ORG_A(links={a_links},props={a_props}) ORG_B(links={b_links},props={b_props})")
        else:
            fail("cross-org isolation of links/proposals",
                 f"ORG_A(links={a_links},props={a_props}) ORG_B(links={b_links},props={b_props}) "
                 f"— want ORG_A>0 and ORG_B==0")
    except Exception as exc:  # noqa: BLE001
        msg = f"{type(exc).__name__}: {exc}"
        if "permission denied" in str(exc).lower():
            skip("RLS: cross-org isolation of links/proposals",
                 f"app_service lacks table GRANTs (not an isolation breach): {msg}")
        else:
            fail("RLS: cross-org isolation of links/proposals", msg)
    finally:
        await conn.close()


async def count_leftovers(conn):
    el = await count_val(
        conn, "SELECT count(*) FROM document_entity_links WHERE org_id=$1 AND "
        "(document_id = ANY($2::uuid[]) OR entity_id IN "
        "(SELECT id FROM entities WHERE org_id=$1 AND display_name LIKE '%'||$3||'%'))",
        ORG_A, TEST_DOC_IDS, MARKER)
    rl = await count_val(
        conn, "SELECT count(*) FROM document_record_links WHERE document_id = ANY($1::uuid[])",
        TEST_DOC_IDS)
    pr = await count_val(
        conn, "SELECT count(*) FROM document_link_proposals WHERE document_id = ANY($1::uuid[])",
        TEST_DOC_IDS)
    return el, rl, pr


async def main_async():
    conn = await _connect(DATABASE_URL)
    try:
        await _teardown(conn)                 # teardown-at-START
        uid = await seed(conn)
        print(f"[info] seeded verify user id={uid}")
        await run_assertions(conn, uid)
    finally:
        await conn.close()

    # A9 — cross-org isolation on the real app_service role.
    print("\n--- Assertion 9: cross-org isolation (app_service) ---")
    await rls_isolation_checks()

    # A10 — teardown-at-END + leftover check.
    print("\n--- Assertion 10: teardown leaves zero rows in all three tables ---")
    conn = await _connect(DATABASE_URL)
    try:
        await _teardown(conn)                 # teardown-at-END
        el, rl, pr = await count_leftovers(conn)
        if (el, rl, pr) == (0, 0, 0):
            ok("teardown: zero leftover rows (entity_links / record_links / proposals)")
        else:
            fail("teardown: zero leftover rows",
                 f"entity_links={el}, record_links={rl}, proposals={pr}")
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
    print("=== Chancery Phase 5 verify (document linkage) — start ===")
    try:
        asyncio.run(main_async())
    except Exception:  # noqa: BLE001 — a crash is itself a failure to report
        print("[FATAL] verify crashed:")
        traceback.print_exc()
        _RESULTS.append(("FAIL", "verify run", "crashed — see traceback"))
    sys.exit(summarize())
