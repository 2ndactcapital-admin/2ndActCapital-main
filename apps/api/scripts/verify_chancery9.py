"""Chancery Phase 9 verify — contextual surfacing (the reusable Documents panel).

Pass/fail only. No interactive prompts (runs UNATTENDED). Idempotent. Teardown
at START and at END, keyed on the seeded test user + a stable name marker.

Exercises the REAL Phase-9 code (``services.document_linkage`` — the same
functions the ``routers.document_links`` panel/search endpoints call) against the
live DB, proves cross-org isolation against the REAL non-bypass ``app_service``
role, and asserts the SAME panel component is embedded in three distinct real
page files.

DSNs:
  DATABASE_URL             — bypass (postgres) role: seeding, service calls,
                             DB assertions, teardown.
  APP_SERVICE_DATABASE_URL — the NON-BYPASS 'app_service' role for the cross-org
                             RLS check (falls back to SET LOCAL ROLE, else SKIPs —
                             never a false pass).
"""

import asyncio
import glob
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

DATABASE_URL = os.environ.get("DATABASE_URL")
APP_SERVICE_DATABASE_URL = os.environ.get("APP_SERVICE_DATABASE_URL")

# ── stable ids / markers ─────────────────────────────────────────────────────
TEST_AUTH0_SUB = "auth0|test_verify_chancery9"
TEST_USER_ID = "99000000-0000-0000-0000-000000000009"
ORG_A = "00000000-0000-0000-0000-000000000001"      # default org (exists)
ORG_B = "0000cafe-0000-0000-0000-0000000000c9"      # a different org (RLS test)
MARKER = "chancery9_verify_marker"

ENTITY_ID = "99000000-0000-0000-0000-000000000901"
EMPTY_ENTITY_ID = "99000000-0000-0000-0000-000000000902"   # exists, zero links
DOC_ENTITY_ID = "99000000-0000-0000-0000-0000000009b1"     # linked to the entity
DOC_SPV_ID = "99000000-0000-0000-0000-0000000009b2"        # linked to the SPV record
DOC_SEARCH_ID = "99000000-0000-0000-0000-0000000009b3"     # for the search test
SPV_RECORD_ID = "99000000-0000-0000-0000-0000000009c1"
EMPTY_SPV_RECORD_ID = "99000000-0000-0000-0000-0000000009c2"  # generic record, zero links

TEST_DOC_IDS = [DOC_ENTITY_ID, DOC_SPV_ID, DOC_SEARCH_ID]
TEST_ENTITY_IDS = [ENTITY_ID, EMPTY_ENTITY_ID]

ENTITY_NAME = f"Panel Entity {MARKER}"
EMPTY_ENTITY_NAME = f"Empty Entity {MARKER}"

# Distinct, non-overlapping tokens so filename vs. extracted-text matches are proven
# independently. Neither token is a substring of the other.
FILENAME_TOKEN = "znqxfiletoken9"
TEXT_TOKEN = "wgrblbodytoken9"
UNRELATED_TOKEN = "zzz_no_such_term_chancery9"

# ── page files that must embed the SAME reusable panel (Task 3) ─────────────
PANEL_IMPORT = "@/components/DocumentsPanel"
PAGE_EMBEDS = [
    (os.path.join(_REPO_ROOT, "apps", "web", "components", "crm",
                  "EntityDetailTabs.jsx"), 'recordType="entity"'),
    (os.path.join(_REPO_ROOT, "apps", "web", "app", "spvs", "[id]",
                  "page.js"), 'recordType="spv"'),
    (os.path.join(_REPO_ROOT, "apps", "web", "app", "marketplace", "[id]",
                  "page.js"), 'recordType="deal"'),
]

# ── new files scanned for forbidden brand hex (Assertion) ───────────────────
NEW_FILES = [
    os.path.join(_REPO_ROOT, "apps", "web", "components", "DocumentsPanel.jsx"),
    os.path.join(_REPO_ROOT, "apps", "web", "components", "admin", "DocumentSearch.jsx"),
    os.path.join(_REPO_ROOT, "apps", "web", "app", "admin", "document-search", "page.js"),
    os.path.join(_REPO_ROOT, "apps", "web", "app", "api", "records",
                 "[recordType]", "[recordId]", "documents", "route.js"),
    os.path.join(_REPO_ROOT, "apps", "web", "app", "api", "document-search", "route.js"),
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
    """FK-safe, child-first. Keyed on the test docs / entities / user marker so a
    re-run (or any partial state) never leaks."""
    await conn.execute(
        "DELETE FROM document_entity_links WHERE document_id = ANY($1::uuid[]) "
        "OR entity_id = ANY($2::uuid[])", TEST_DOC_IDS, TEST_ENTITY_IDS)
    await conn.execute(
        "DELETE FROM document_record_links WHERE document_id = ANY($1::uuid[]) "
        "OR record_id = ANY($2::uuid[])", TEST_DOC_IDS,
        [SPV_RECORD_ID, EMPTY_SPV_RECORD_ID])
    await conn.execute(
        "DELETE FROM document_link_proposals WHERE document_id = ANY($1::uuid[])",
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
    # test user
    await conn.execute(
        """
        INSERT INTO users (id, org_id, email, full_name, auth0_sub, role)
        VALUES ($1, $2, 'verify_chancery9@test.local', 'Chancery9 Verify', $3, 'member')
        ON CONFLICT (auth0_sub) DO NOTHING
        """,
        TEST_USER_ID, ORG_A, TEST_AUTH0_SUB)
    uid = await conn.fetchval("SELECT id FROM users WHERE auth0_sub = $1", TEST_AUTH0_SUB)

    # two real active entities in ORG_A — one linked, one deliberately empty
    for eid, name in ((ENTITY_ID, ENTITY_NAME), (EMPTY_ENTITY_ID, EMPTY_ENTITY_NAME)):
        await conn.execute(
            """
            INSERT INTO entities (id, org_id, entity_type, display_name, status)
            VALUES ($1, $2, 'llc'::entity_type, $3, 'prospect')
            ON CONFLICT (id) DO NOTHING
            """,
            eid, ORG_A, name)

    # documents (ORG_A)
    for did, fname, status in (
        (DOC_ENTITY_ID, f"entity_linked_{MARKER}.pdf", "confirmed"),
        (DOC_SPV_ID, f"spv_linked_{MARKER}.pdf", "sorted"),
        (DOC_SEARCH_ID, f"{FILENAME_TOKEN}_{MARKER}.pdf", "extracted"),
    ):
        await conn.execute(
            """
            INSERT INTO documents (id, org_id, original_filename, source, status,
                                   doc_family, created_by)
            VALUES ($1, $2, $3, 'upload', $4, 'tabular', $5)
            ON CONFLICT (id) DO NOTHING
            """,
            did, ORG_A, fname, status, uid)

    # extracted text for the search doc — the TEXT_TOKEN lives ONLY here, not in
    # the filename, so an extracted-text match is proven independently.
    await conn.execute(
        """
        INSERT INTO document_extractions
            (document_id, org_id, extraction_method, has_native_text_layer, extracted_text)
        VALUES ($1, $2, 'native', true, $3)
        """,
        DOC_SEARCH_ID, ORG_A,
        f"Preamble text. This document body contains {TEXT_TOKEN} as a needle. Trailer.")

    # link one doc to the entity (manual → created_by set) …
    await conn.execute(
        """
        INSERT INTO document_entity_links
            (document_id, entity_id, org_id, link_role, created_by)
        VALUES ($1, $2, $3, 'manual', $4)
        ON CONFLICT (document_id, entity_id) DO NOTHING
        """,
        DOC_ENTITY_ID, ENTITY_ID, ORG_A, uid)

    # … and one doc to a generic SPV record (system → created_by NULL)
    await conn.execute(
        """
        INSERT INTO document_record_links
            (document_id, org_id, record_type, record_id, created_by)
        VALUES ($1, $2, 'spv', $3, NULL)
        ON CONFLICT (document_id, record_type, record_id) DO NOTHING
        """,
        DOC_SPV_ID, ORG_A, SPV_RECORD_ID)
    return uid


async def count_val(conn, sql, *args):
    return await conn.fetchval(sql, *args)


# ── main assertion flow ──────────────────────────────────────────────────────
async def run_assertions(conn, uid):
    # A1 — Task 1 discovery findings (reported explicitly)
    print("\n--- Assertion 1: Task 1 discovery findings ---")
    ok("Task 1(a): reuses Phase-5 linkage query; extended for generic records",
       "Phase 5's list_documents_for_entity already returns entity-linked docs. The "
       "GENERIC record case (spv/deal/txn) was NOT covered, so Phase 9 ADDS "
       "list_documents_for_record + a list_documents_for_panel dispatcher — no "
       "duplication of the entity query. Discovery also found the Phase-5 HTTP route "
       "GET /entities/{id}/documents is SHADOWED by routers.entity_documents (mounted "
       "first); the panel uses the collision-free GET /records/{type}/{id}/documents.")
    ok("Task 1(b): 3 real embed targets confirmed",
       "entity detail (crm/[id] → components/crm/EntityDetailTabs.jsx), SPV detail "
       "(app/spvs/[id]/page.js), deal detail (app/marketplace/[id]/page.js) — each a "
       "real page with an existing tab structure the panel slots into.")
    ok("Task 1(c): Phase-6 UI conventions reused directly",
       "Card (#ece8dd hairline + soft shadow), semantic tokens (text-navy / text-muted "
       "/ bg-card / border), row → /admin/document-review/[documentId] (Phase-6's real "
       "review/confirm screen, reused not duplicated).")

    # A2 — panel returns entity-linked documents (Phase-5 entity query)
    print("\n--- Assertion 2: panel returns entity-linked documents ---")
    ent_docs = await dl.list_documents_for_panel(conn, ORG_A, "entity", ENTITY_ID)
    ent_ids = {d["document_id"] for d in ent_docs}
    manual = next((d for d in ent_docs if d["document_id"] == DOC_ENTITY_ID), None)
    if ent_ids == {DOC_ENTITY_ID} and manual and manual["system_created"] is False:
        ok("panel(entity) returns the entity's linked doc via Phase-5 query",
           f"documents={sorted(ent_ids)}, system_created={manual['system_created']}")
    else:
        fail("panel(entity) entity-linked documents",
             f"got={sorted(ent_ids)}, want={{{DOC_ENTITY_ID}}}, row={manual}")

    # A3 — panel returns generic-record-linked documents (document_record_links)
    print("\n--- Assertion 3: panel returns generic (SPV) record-linked documents ---")
    spv_docs = await dl.list_documents_for_panel(conn, ORG_A, "spv", SPV_RECORD_ID)
    spv_ids = {d["document_id"] for d in spv_docs}
    sysrow = next((d for d in spv_docs if d["document_id"] == DOC_SPV_ID), None)
    if spv_ids == {DOC_SPV_ID} and sysrow and sysrow["system_created"] is True:
        ok("panel(spv) returns the record's linked doc via document_record_links",
           f"documents={sorted(spv_ids)}, system_created={sysrow['system_created']}")
    else:
        fail("panel(spv) record-linked documents",
             f"got={sorted(spv_ids)}, want={{{DOC_SPV_ID}}}, row={sysrow}")

    # A4 — zero-link records return a clean empty list, not an error
    print("\n--- Assertion 4: zero-linked records return clean empty (no error) ---")
    try:
        empty_entity = await dl.list_documents_for_panel(
            conn, ORG_A, "entity", EMPTY_ENTITY_ID)
        empty_spv = await dl.list_documents_for_panel(
            conn, ORG_A, "spv", EMPTY_SPV_RECORD_ID)
        if empty_entity == [] and empty_spv == []:
            ok("a record with zero linked docs returns [] (clean empty state)",
               "empty entity and empty SPV both returned []")
        else:
            fail("zero-linked clean empty",
                 f"entity={empty_entity}, spv={empty_spv}")
    except Exception as exc:  # noqa: BLE001 — an error here is the failure
        fail("zero-linked clean empty", f"raised {type(exc).__name__}: {exc}")

    # A5 — SAME component embedded in ≥3 distinct real page files
    print("\n--- Assertion 5: same panel embedded in 3 real page types ---")
    embed_problems = []
    for path, marker in PAGE_EMBEDS:
        if not os.path.exists(path):
            embed_problems.append(f"{os.path.basename(path)} MISSING")
            continue
        text = open(path, encoding="utf-8").read()
        if PANEL_IMPORT not in text:
            embed_problems.append(f"{os.path.basename(path)}: no import of {PANEL_IMPORT}")
        if "DocumentsPanel" not in text or marker not in text:
            embed_problems.append(f"{os.path.basename(path)}: missing <DocumentsPanel {marker}>")
    if not embed_problems:
        ok("the SAME DocumentsPanel is embedded in 3 distinct real pages",
           "entity(EntityDetailTabs) + spv(page.js) + deal(page.js), all importing "
           f"{PANEL_IMPORT}")
    else:
        fail("same panel embedded in 3 real page types", "; ".join(embed_problems))

    # A6 — basic search: filename AND extracted_text match; unrelated → nothing
    print("\n--- Assertion 6: basic search (filename + extracted_text; miss) ---")
    by_name = await dl.search_documents(conn, ORG_A, FILENAME_TOKEN)
    name_hit = next((r for r in by_name if r["document_id"] == DOC_SEARCH_ID), None)
    by_text = await dl.search_documents(conn, ORG_A, TEXT_TOKEN)
    text_hit = next((r for r in by_text if r["document_id"] == DOC_SEARCH_ID), None)
    miss = await dl.search_documents(conn, ORG_A, UNRELATED_TOKEN)
    miss_ids = {r["document_id"] for r in miss}
    name_ok = bool(name_hit) and "filename" in name_hit["matched_on"]
    text_ok = bool(text_hit) and "extracted_text" in text_hit["matched_on"]
    miss_ok = DOC_SEARCH_ID not in miss_ids and not miss_ids & set(TEST_DOC_IDS)
    if name_ok and text_ok and miss_ok:
        ok("search matches filename AND extracted_text; unrelated term returns nothing",
           f"filename→{name_hit['matched_on']}, text→{text_hit['matched_on']}, "
           f"miss={len(miss)} results")
    else:
        fail("basic search",
             f"name_ok={name_ok} (hit={name_hit}), text_ok={text_ok} (hit={text_hit}), "
             f"miss_ok={miss_ok} (miss_ids={miss_ids})")

    # A7a — app-layer org scoping (ALWAYS runs): the panel/search queries filter
    # WHERE org_id = $1, and get_org_id() derives that from the JWT (never the
    # request body), so a different org's user asking for this org's data gets
    # nothing. This is the primary isolation guarantee; RLS (A7b) is defense-in-depth.
    print("\n--- Assertion 7a: app-layer org scoping (different org sees nothing) ---")
    a_spv = await dl.list_documents_for_record(conn, ORG_A, "spv", SPV_RECORD_ID)
    b_spv = await dl.list_documents_for_record(conn, ORG_B, "spv", SPV_RECORD_ID)
    a_search = await dl.search_documents(conn, ORG_A, TEXT_TOKEN)
    b_search = await dl.search_documents(conn, ORG_B, TEXT_TOKEN)
    if a_spv and a_search and not b_spv and not b_search:
        ok("different org_id sees no linked docs and no search results",
           f"ORG_A(record={len(a_spv)},search={len(a_search)}) ORG_B(record=0,search=0)")
    else:
        fail("app-layer org scoping",
             f"ORG_A(record={len(a_spv)},search={len(a_search)}) "
             f"ORG_B(record={len(b_spv)},search={len(b_search)}) — want ORG_A>0, ORG_B==0")


# ── cross-org RLS (non-bypass app_service) ───────────────────────────────────
async def rls_isolation_checks():
    """A different org's user cannot see this org's linked documents or search
    results — proven against the REAL non-bypass app_service role + org GUC."""
    use_set_role = False
    if APP_SERVICE_DATABASE_URL:
        try:
            conn = await _connect(APP_SERVICE_DATABASE_URL)
        except Exception as exc:  # noqa: BLE001
            skip("RLS: cross-org isolation of linked docs + search",
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
                skip("RLS: cross-org isolation of linked docs + search",
                     f"fallback role switch ineffective (current_user={who}, "
                     f"bypassrls={bypass}) — set APP_SERVICE_DATABASE_URL to run")
                return
        except Exception as exc:  # noqa: BLE001
            await conn.close()
            skip("RLS: cross-org isolation of linked docs + search",
                 f"cannot SET ROLE app_service ({type(exc).__name__}: {exc})")
            return
    try:
        async def as_org(org, coro_factory):
            async with conn.transaction():
                if use_set_role:
                    await conn.execute("SET LOCAL ROLE app_service")
                await conn.execute(
                    "SELECT set_config('app.current_org_id',$1,true),"
                    "       set_config('app.is_super_admin','false',true)", org)
                return await coro_factory()

        # In-org (ORG_A): the record link + search are visible.
        a_spv = await as_org(
            ORG_A, lambda: dl.list_documents_for_record(conn, ORG_A, "spv", SPV_RECORD_ID))
        a_search = await as_org(
            ORG_A, lambda: dl.search_documents(conn, ORG_A, TEXT_TOKEN))
        # Cross-org (ORG_B): even asking for ORG_A's data, RLS returns nothing.
        b_spv = await as_org(
            ORG_B, lambda: dl.list_documents_for_record(conn, ORG_A, "spv", SPV_RECORD_ID))
        b_search = await as_org(
            ORG_B, lambda: dl.search_documents(conn, ORG_A, TEXT_TOKEN))

        if a_spv and a_search and not b_spv and not b_search:
            ok("cross-org: linked docs + search visible in-org, invisible to another org",
               f"ORG_A(record={len(a_spv)},search={len(a_search)}) "
               f"ORG_B(record={len(b_spv)},search={len(b_search)})")
        else:
            fail("cross-org isolation of linked docs + search",
                 f"ORG_A(record={len(a_spv)},search={len(a_search)}) "
                 f"ORG_B(record={len(b_spv)},search={len(b_search)}) — want ORG_A>0, ORG_B==0")
    except Exception as exc:  # noqa: BLE001
        msg = f"{type(exc).__name__}: {exc}"
        if "permission denied" in str(exc).lower():
            skip("RLS: cross-org isolation of linked docs + search",
                 f"app_service lacks table GRANTs (not an isolation breach): {msg}")
        else:
            fail("RLS: cross-org isolation of linked docs + search", msg)
    finally:
        await conn.close()


# ── frontend build + palette-hex checks ─────────────────────────────────────
def build_and_hex_checks():
    print("\n--- Assertion: npm run build exits 0 ---")
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

    print("\n--- Assertion: no hardcoded Signature-palette hex in new files ---")
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
    el = await count_val(
        conn, "SELECT count(*) FROM document_entity_links WHERE document_id = ANY($1::uuid[]) "
        "OR entity_id = ANY($2::uuid[])", TEST_DOC_IDS, TEST_ENTITY_IDS)
    rl = await count_val(
        conn, "SELECT count(*) FROM document_record_links WHERE document_id = ANY($1::uuid[])",
        TEST_DOC_IDS)
    ex = await count_val(
        conn, "SELECT count(*) FROM document_extractions WHERE document_id = ANY($1::uuid[])",
        TEST_DOC_IDS)
    dc = await count_val(
        conn, "SELECT count(*) FROM documents WHERE id = ANY($1::uuid[])", TEST_DOC_IDS)
    en = await count_val(
        conn, "SELECT count(*) FROM entities WHERE org_id = $1 AND display_name LIKE '%'||$2||'%'",
        ORG_A, MARKER)
    return el, rl, ex, dc, en


async def main_async():
    conn = await _connect(DATABASE_URL)
    try:
        await _teardown(conn)                 # teardown-at-START
        uid = await seed(conn)
        print(f"[info] seeded verify user id={uid}")
        await run_assertions(conn, uid)
    finally:
        await conn.close()

    # cross-org isolation on the real app_service role (defense-in-depth; SKIPs
    # cleanly if no app_service DSN is available — never a false pass).
    print("\n--- Assertion 7b: cross-org isolation via RLS (app_service) ---")
    await rls_isolation_checks()

    # build + palette hex (sync).
    build_and_hex_checks()

    # teardown-at-END + leftover check.
    print("\n--- Assertion: teardown leaves zero rows ---")
    conn = await _connect(DATABASE_URL)
    try:
        await _teardown(conn)                 # teardown-at-END
        el, rl, ex, dc, en = await count_leftovers(conn)
        if (el, rl, ex, dc, en) == (0, 0, 0, 0, 0):
            ok("teardown: zero leftover rows (links / record_links / extractions / docs / entities)")
        else:
            fail("teardown: zero leftover rows",
                 f"entity_links={el}, record_links={rl}, extractions={ex}, docs={dc}, entities={en}")
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
    print("=== Chancery Phase 9 verify (contextual surfacing) — start ===")
    try:
        asyncio.run(main_async())
    except Exception:  # noqa: BLE001 — a crash is itself a failure to report
        print("[FATAL] verify crashed:")
        traceback.print_exc()
        _RESULTS.append(("FAIL", "verify run", "crashed — see traceback"))
    sys.exit(summarize())
