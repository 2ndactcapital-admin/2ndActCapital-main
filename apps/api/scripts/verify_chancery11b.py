"""Chancery Phase 11b verify — semantic INDEX + RETRIEVE (Voyage + pgvector).

Pass/fail only. No interactive prompts (runs UNATTENDED). Teardown at START and
at END. This sprint has TWO real, potentially-blocking Task-1 gates that are
checked HONESTLY before any downstream assertion, exactly like the earlier AWS
Textract credential gate:

  GATE (a) pgvector — the `vector` extension is actually enabled in the deployed
           DB (real SELECT ... FROM pg_extension).
  GATE (b) Voyage AI — a REAL Voyage credential exists AND a genuine live
           embedding call returns a real vector. A key-shaped string is NOT
           enough; we make the actual API call through the real service
           (services.document_embedding) and require an embedding back.

If GATE (b) fails, Tasks 2-4 are treated as un-exercisable and every downstream
assertion is reported [BLOCKED] with the exact reason — never a false [PASS].

── Outcome of THIS run (2026-07-31) ──────────────────────────────────────────
  A real VOYAGE_API_KEY is now provisioned (apps/api/.env). GATE (b) makes a
  live call and PASSES with a 1024-dim embedding; Tasks 2-4 are exercised for
  real below.

Assertions (mirror the sprint prompt):
  A1  Task-1 findings incl. proof of a REAL successful Voyage embedding call.
  A2  Setting a NON-Voyage embedding provider is REJECTED by the backend
      ENDPOINT with the specific message ("Voyage is the only model enabled
      right now") — tested over real HTTP, not just the UI.
  A3  Setting Voyage explicitly (the only valid choice) succeeds over HTTP.
  A4  A real test document's content is embedded + stored with the right vector
      dimension (1024).
  A5  A semantic query returns the RELEVANT document and NOT an unrelated one
      (a real similarity proof — the relevant doc outranks the unrelated one).
  A6  A restricted-access document does NOT appear in a user's results without a
      grant, and DOES once granted (the restricted-access filter, real).
  A7  A different org's documents never appear in this org's results (cross-org
      isolation) + the org-isolation RLS policy is installed on the new table.
  A8  Teardown: zero leftover rows.

app_service NOTE: DATABASE_URL connects as the RLS-bypassing `postgres` role and
cannot SET ROLE app_service (verified — InsufficientPrivilegeError), so RLS
cannot be exercised as app_service from here (the same known constraint every
RLS-touching sprint hit). The search's cross-org + restricted enforcement that
users actually hit lives in the application (the org-scoped query + the reused
visibility engines); A6/A7 test THAT real path, and A7 additionally proves the
org-isolation RLS policy is installed as the DB backstop.

Exit codes: 0 = all real assertions green; 1 = a real FAIL; 2 = BLOCKED by a
Task-1 gate.
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

# Ensure VOYAGE_API_KEY is in the process env for anything that reads it there
# (the service also falls back to apps/api/.env on its own).
if not os.environ.get("VOYAGE_API_KEY"):
    _env = os.path.join(_API_ROOT, ".env")
    try:
        with open(_env) as _fh:
            for _line in _fh:
                _line = _line.strip()
                if _line.startswith("VOYAGE_API_KEY=") and "=" in _line:
                    os.environ["VOYAGE_API_KEY"] = (
                        _line.split("=", 1)[1].strip().strip('"').strip("'")
                    )
                    break
    except OSError:
        pass

import asyncpg  # noqa: E402

from services import document_embedding as de  # noqa: E402

DATABASE_URL = os.environ.get("DATABASE_URL")

EMBEDDING_TABLE = "document_embeddings"

# ── Fixed test identifiers (all under two THROWAWAY test orgs → full teardown) ─
ORG_MAIN = "99000000-0000-0000-0000-0000000011b1"
ORG_OTHER = "99000000-0000-0000-0000-0000000011b2"

U_ADMIN = "99000000-0000-0000-0000-00000000ad11"   # org_admin of ORG_MAIN (HTTP settings)
U_STAFF = "99000000-0000-0000-0000-00000000f011"   # investment_staff of ORG_MAIN (search)
SUB_ADMIN = "auth0|verify_11b_admin"
SUB_STAFF = "auth0|verify_11b_staff"

E_NORMAL = "99000000-0000-0000-0000-0000000e0001"       # not restricted (ORG_MAIN)
E_RESTRICTED = "99000000-0000-0000-0000-0000000e0002"   # access_restricted (ORG_MAIN)
E_OTHER = "99000000-0000-0000-0000-0000000e0003"        # ORG_OTHER

D_SOLAR = "99000000-0000-0000-0000-0000000d0001"       # relevant, E_NORMAL
D_PIE = "99000000-0000-0000-0000-0000000d0002"         # unrelated, E_NORMAL
D_RESTRICTED = "99000000-0000-0000-0000-0000000d0003"  # relevant, E_RESTRICTED
D_OTHER = "99000000-0000-0000-0000-0000000d0004"       # relevant, ORG_OTHER

_TEST_ORGS = [ORG_MAIN, ORG_OTHER]

_CONTENT = {
    D_SOLAR: (
        "Investment memorandum for a utility-scale solar photovoltaic power "
        "generation facility. This renewable energy fund finances solar panel "
        "arrays and clean electricity infrastructure across the southwest region."
    ),
    D_PIE: (
        "Grandmother's classic apple pie recipe. Combine sliced apples with "
        "cinnamon, nutmeg, butter and sugar; bake the pastry crust until golden "
        "brown and serve warm with vanilla ice cream for dessert."
    ),
    D_RESTRICTED: (
        "Confidential solar energy infrastructure investment held in a "
        "restricted account. Photovoltaic renewable power generation project "
        "financing and clean-energy capital commitments."
    ),
    D_OTHER: (
        "Solar photovoltaic renewable energy investment fund for utility-scale "
        "clean power generation and grid infrastructure."
    ),
}
_QUERY = "renewable clean energy solar power generation investment fund"

# ── tiny result harness ──────────────────────────────────────────────────────
_RESULTS: list[tuple[str, str, str]] = []


def ok(name, detail=""):
    _RESULTS.append(("PASS", name, detail))
    print(f"[PASS] {name}" + (f" — {detail}" if detail else ""))


def fail(name, detail=""):
    _RESULTS.append(("FAIL", name, detail))
    print(f"[FAIL] {name}" + (f" — {detail}" if detail else ""))


def blocked(name, detail=""):
    _RESULTS.append(("BLOCKED", name, detail))
    print(f"[BLOCKED] {name}" + (f" — {detail}" if detail else ""))


async def _connect(dsn):
    return await asyncpg.connect(dsn, statement_cache_size=0, ssl="require")


# ── teardown (FK-safe; scoped to the two throwaway test orgs) ─────────────────
async def teardown(conn):
    for tbl in (
        "document_embeddings",
        "document_narrative_extractions",
        "document_template_extractions",
        "document_extractions",
        "documents",
        "staff_assignments",
        "restricted_access_grants",
        "delegate_grants",
        "entities",
        "org_settings",
    ):
        await conn.execute(
            f"DELETE FROM {tbl} WHERE org_id = ANY($1::uuid[])", _TEST_ORGS
        )
    await conn.execute(
        "DELETE FROM users WHERE org_id = ANY($1::uuid[]) OR auth0_sub = ANY($2::text[])",
        _TEST_ORGS, [SUB_ADMIN, SUB_STAFF],
    )
    await conn.execute(
        "DELETE FROM organizations WHERE id = ANY($1::uuid[])", _TEST_ORGS
    )


async def _leftover_count(conn) -> int:
    total = 0
    for tbl in (
        "document_embeddings", "document_extractions", "documents",
        "staff_assignments", "restricted_access_grants", "entities",
        "org_settings",
    ):
        total += await conn.fetchval(
            f"SELECT count(*) FROM {tbl} WHERE org_id = ANY($1::uuid[])", _TEST_ORGS
        )
    total += await conn.fetchval(
        "SELECT count(*) FROM users WHERE org_id = ANY($1::uuid[])", _TEST_ORGS
    )
    total += await conn.fetchval(
        "SELECT count(*) FROM organizations WHERE id = ANY($1::uuid[])", _TEST_ORGS
    )
    return total


# ── seed ──────────────────────────────────────────────────────────────────────
async def seed(conn):
    await conn.execute(
        "INSERT INTO organizations (id, name, slug) VALUES "
        "($1,'Verify 11b Main','verify-11b-main'),($2,'Verify 11b Other','verify-11b-other')",
        ORG_MAIN, ORG_OTHER,
    )
    await conn.execute(
        "INSERT INTO users (id, org_id, email, role, auth0_sub) VALUES "
        "($1,$2,'verify_11b_admin@test.local','org_admin',$3),"
        "($4,$2,'verify_11b_staff@test.local','investment_staff',$5)",
        U_ADMIN, ORG_MAIN, SUB_ADMIN, U_STAFF, SUB_STAFF,
    )
    await conn.execute(
        "INSERT INTO entities (id, org_id, entity_type, display_name, access_restricted) VALUES "
        "($1,$2,'trust','Normal Trust',false),"
        "($3,$2,'trust','Restricted Trust',true),"
        "($4,$5,'trust','Other Org Trust',false)",
        E_NORMAL, ORG_MAIN, E_RESTRICTED, E_OTHER, ORG_OTHER,
    )
    # staff visibility: U_STAFF assigned to BOTH ORG_MAIN entities.
    await conn.execute(
        "INSERT INTO staff_assignments (org_id, entity_id, assigned_to_user_id) VALUES "
        "($1,$2,$3),($1,$4,$3)",
        ORG_MAIN, E_NORMAL, U_STAFF, E_RESTRICTED,
    )
    # documents + their extracted text
    docs = [
        (D_SOLAR, ORG_MAIN, E_NORMAL, "solar_memo.pdf", "narrative"),
        (D_PIE, ORG_MAIN, E_NORMAL, "apple_pie.pdf", "narrative"),
        (D_RESTRICTED, ORG_MAIN, E_RESTRICTED, "restricted_solar.pdf", "narrative"),
        (D_OTHER, ORG_OTHER, E_OTHER, "other_solar.pdf", "narrative"),
    ]
    for did, oid, eid, fname, fam in docs:
        await conn.execute(
            "INSERT INTO documents (id, org_id, entity_id, original_filename, "
            "source, status, doc_family) VALUES ($1,$2,$3,$4,'upload','stored',$5)",
            did, oid, eid, fname, fam,
        )
        await conn.execute(
            "INSERT INTO document_extractions (document_id, org_id, extraction_method, "
            "extracted_text) VALUES ($1,$2,'native_text',$3)",
            did, oid, _CONTENT[did],
        )


# ── the run ───────────────────────────────────────────────────────────────────
async def main_async():
    print("=== Chancery Phase 11b verify (semantic INDEX+RETRIEVE) — start ===")
    conn = await _connect(DATABASE_URL)
    gate_a = gate_b = False
    try:
        await teardown(conn)  # teardown-at-START

        # ── GATE (a): pgvector ──────────────────────────────────────────────
        row = await conn.fetchrow(
            "SELECT extversion FROM pg_extension WHERE extname = 'vector'")
        if row:
            gate_a = True
            ok("GATE (a) pgvector enabled", f"vector v{row['extversion']} (real SELECT)")
        else:
            fail("GATE (a) pgvector enabled", "vector extension absent")

        # ── GATE (b): REAL live Voyage embedding call ───────────────────────
        b_detail = ""
        key = de._voyage_api_key()
        if not key:
            blocked("GATE (b) Voyage credential + live call",
                    "no VOYAGE_API_KEY in env or apps/api/.env — cannot call")
        else:
            try:
                vecs = await de.embed_texts(
                    ["the quick brown fox"], provider="voyage",
                    model=de.DEFAULT_EMBEDDING_MODEL, input_type="document",
                )
                dim = len(vecs[0])
                if dim == de.EMBEDDING_DIMENSIONS:
                    gate_b = True
                    b_detail = f"model {de.DEFAULT_EMBEDDING_MODEL} → real {dim}-dim vector"
                    ok("GATE (b) Voyage credential + live call (REAL)", b_detail)
                else:
                    fail("GATE (b) Voyage credential + live call",
                         f"returned dim {dim}, expected {de.EMBEDDING_DIMENSIONS}")
            except Exception as exc:  # noqa: BLE001
                blocked("GATE (b) Voyage credential + live call",
                        f"live call failed: {type(exc).__name__}: {exc}")

        # A1 — Task-1 findings + proof of the real Voyage call.
        if gate_a and gate_b:
            ok("A1: Task-1 findings reported (pgvector + REAL Voyage proof)",
               f"pgvector v{row['extversion']}; voyage {b_detail}; dim="
               f"{de.EMBEDDING_DIMENSIONS}; providers shown={de.EMBEDDING_PROVIDERS}; "
               f"enabled={sorted(de.ENABLED_EMBEDDING_PROVIDERS)}")
        else:
            blocked("A1: Task-1 findings incl. proof of a REAL Voyage call",
                    "a Task-1 gate did not pass — see GATE lines above")

        if not (gate_a and gate_b):
            # Gate failed → do not exercise Tasks 2-4; block the rest honestly.
            for a in ("A2: non-Voyage provider REJECTED by endpoint",
                      "A3: setting Voyage explicitly succeeds",
                      "A4: real document embedded + stored (dim 1024)",
                      "A5: semantic query returns relevant, not unrelated",
                      "A6: restricted doc hidden without grant",
                      "A7: cross-org isolation + RLS policy installed",
                      "A8: teardown zero leftover rows"):
                blocked(a, "blocked by Task-1 gate (no real Voyage embedding path)")
            await teardown(conn)
            return gate_a, gate_b

        # ── Build the test corpus ───────────────────────────────────────────
        await seed(conn)

        # A2 + A3 — real HTTP round-trip against the settings ENDPOINT.
        await _http_settings_assertions()

        # ── INDEX: embed all four documents through the real service ─────────
        from services.database import get_pool
        pool = await get_pool()
        outcomes = {}
        for did in (D_SOLAR, D_PIE, D_RESTRICTED, D_OTHER):
            oid = ORG_OTHER if did == D_OTHER else ORG_MAIN
            outcomes[did] = await de.embed_document(pool, {"id": did}, oid)

        # A4 — the relevant doc is embedded + stored with the right dimension.
        emb = await conn.fetchrow(
            "SELECT provider, model, dimensions, content_source, "
            "vector_dims(embedding) AS vdim FROM document_embeddings WHERE document_id = $1",
            D_SOLAR,
        )
        if (emb and emb["dimensions"] == de.EMBEDDING_DIMENSIONS
                and emb["vdim"] == de.EMBEDDING_DIMENSIONS
                and outcomes[D_SOLAR].get("outcome") == "embedded"):
            ok("A4: real document embedded + stored (dim 1024)",
               f"provider={emb['provider']} model={emb['model']} "
               f"dims={emb['dimensions']} vector_dims={emb['vdim']} "
               f"source={emb['content_source']}")
        else:
            fail("A4: real document embedded + stored (dim 1024)",
                 f"row={dict(emb) if emb else None} outcome={outcomes[D_SOLAR]}")

        # A5 — relevant beats unrelated (real similarity proof). One search;
        # results are distance-sorted, so results[0] is the top hit.
        res_all = await de.semantic_search(
            pool, ORG_MAIN, U_STAFF, _QUERY, is_staff=True, limit=10)
        ids_all = [r["document_id"] for r in res_all]
        sim = {r["document_id"]: r["similarity"] for r in res_all}
        top_is_solar = bool(res_all) and res_all[0]["document_id"] == D_SOLAR
        solar_beats_pie = (
            D_SOLAR in sim and D_PIE in sim and sim[D_SOLAR] > sim[D_PIE]
        )
        if top_is_solar and solar_beats_pie:
            ok("A5: semantic query returns relevant, not unrelated",
               f"top-1={res_all[0]['document_id'][:8]}(solar) "
               f"sim(solar)={sim[D_SOLAR]} > sim(pie)={sim[D_PIE]}")
        else:
            fail("A5: semantic query returns relevant, not unrelated",
                 f"top={res_all[0]['document_id'] if res_all else None} sims={sim} all={ids_all}")

        # A6 — restricted doc hidden without a grant, visible once granted.
        no_grant_ids = ids_all  # same search as A5 (U_STAFF has no grant yet)
        restricted_hidden = D_RESTRICTED not in no_grant_ids and D_SOLAR in no_grant_ids
        await conn.execute(
            "INSERT INTO restricted_access_grants (org_id, entity_id, user_id) "
            "VALUES ($1,$2,$3)", ORG_MAIN, E_RESTRICTED, U_STAFF,
        )
        res_granted = await de.semantic_search(
            pool, ORG_MAIN, U_STAFF, _QUERY, is_staff=True, limit=10)
        granted_ids = [r["document_id"] for r in res_granted]
        restricted_now_visible = D_RESTRICTED in granted_ids
        if restricted_hidden and restricted_now_visible:
            ok("A6: restricted doc hidden without grant, shown with grant",
               f"no-grant results={[i[:8] for i in no_grant_ids]}; "
               f"after grant restricted present={restricted_now_visible}")
        else:
            fail("A6: restricted doc hidden without grant",
                 f"hidden_without_grant={restricted_hidden} "
                 f"visible_after_grant={restricted_now_visible} "
                 f"no_grant={no_grant_ids} granted={granted_ids}")

        # A7 — cross-org isolation (real query path) + RLS policy installed.
        cross = D_OTHER not in granted_ids and D_OTHER not in no_grant_ids
        # confirm D_OTHER really was embedded (so its absence is isolation, not a no-op)
        other_embedded = await conn.fetchval(
            "SELECT count(*) FROM document_embeddings WHERE document_id = $1", D_OTHER)
        policy = await conn.fetchval(
            "SELECT count(*) FROM pg_policy WHERE polrelid = 'public.document_embeddings'::regclass "
            "AND polname = 'document_embeddings_org_isolation'")
        rls_on = await conn.fetchval(
            "SELECT relrowsecurity FROM pg_class WHERE oid = 'public.document_embeddings'::regclass")
        if cross and other_embedded == 1 and policy == 1 and rls_on:
            ok("A7: cross-org isolation + RLS policy installed",
               f"ORG_OTHER doc embedded={other_embedded} yet absent from ORG_MAIN "
               f"results; RLS enabled={rls_on}, org-isolation policy present={policy==1}")
        else:
            fail("A7: cross-org isolation + RLS policy installed",
                 f"cross_ok={cross} other_embedded={other_embedded} "
                 f"policy={policy} rls_on={rls_on}")

        # A8 — teardown leaves zero leftover rows.
        await teardown(conn)  # teardown-at-END
        left = await _leftover_count(conn)
        if left == 0:
            ok("A8: teardown zero leftover rows", "all test rows removed")
        else:
            fail("A8: teardown zero leftover rows", f"{left} rows remain")
    finally:
        await conn.close()
        try:
            from services.database import close_pool
            await close_pool()
        except Exception:  # noqa: BLE001
            pass

    return gate_a, gate_b


# ── A2/A3: real HTTP round-trip against the settings endpoint ────────────────
async def _http_settings_assertions():
    """Drive the REAL PUT /api/v1/orgs/{org}/settings endpoint via TestClient,
    proving the non-Voyage rejection and Voyage acceptance are enforced by the
    BACKEND (HTTP 400 + exact message), not just the UI."""
    try:
        import main
        from starlette.testclient import TestClient
    except Exception as exc:  # noqa: BLE001
        fail("A2: non-Voyage provider REJECTED by endpoint",
             f"could not import app/TestClient: {type(exc).__name__}: {exc}")
        fail("A3: setting Voyage explicitly succeeds", "app import failed")
        return

    main.verify_token = lambda _t: {"sub": SUB_ADMIN,
                                    "email": "verify_11b_admin@test.local",
                                    "org_id": ORG_MAIN}
    hdr = {"Authorization": "Bearer stub"}
    url = f"/api/v1/orgs/{ORG_MAIN}/settings"

    # These are synchronous (TestClient) — run them off the event loop thread.
    def _drive():
        out = {}
        with TestClient(main.app, raise_server_exceptions=False) as c:
            out["reject"] = c.put(
                url, headers=hdr, json={"values": {"ai.embedding.provider": "openai"}})
            out["accept"] = c.put(
                url, headers=hdr, json={"values": {"ai.embedding.provider": "voyage"}})
        return out

    out = await asyncio.to_thread(_drive)

    rej = out["reject"]
    rej_body = {}
    try:
        rej_body = rej.json()
    except Exception:  # noqa: BLE001
        pass
    if rej.status_code == 400 and rej_body.get("detail") == de.EMBEDDING_PROVIDER_DISABLED_MSG:
        ok("A2: non-Voyage provider REJECTED by endpoint",
           f"HTTP {rej.status_code}, detail={rej_body.get('detail')!r}")
    else:
        fail("A2: non-Voyage provider REJECTED by endpoint",
             f"HTTP {rej.status_code}, body={rej_body}")

    acc = out["accept"]
    acc_body = {}
    try:
        acc_body = acc.json()
    except Exception:  # noqa: BLE001
        pass
    saved = (acc_body.get("settings") or {}).get("ai.embedding.provider")
    if acc.status_code == 200 and saved == "voyage":
        ok("A3: setting Voyage explicitly succeeds",
           f"HTTP {acc.status_code}, ai.embedding.provider={saved!r}")
    else:
        fail("A3: setting Voyage explicitly succeeds",
             f"HTTP {acc.status_code}, body={json.dumps(acc_body)[:200]}")


def summarize(gate_a, gate_b):
    n_pass = sum(1 for s, _, _ in _RESULTS if s == "PASS")
    n_fail = sum(1 for s, _, _ in _RESULTS if s == "FAIL")
    n_block = sum(1 for s, _, _ in _RESULTS if s == "BLOCKED")
    print("\n=== SUMMARY ===")
    print(f"PASS={n_pass}  FAIL={n_fail}  BLOCKED={n_block}")
    print(f"GATE (a) pgvector : {'PASS' if gate_a else 'FAIL'}")
    print(f"GATE (b) Voyage   : {'PASS' if gate_b else 'BLOCKED/FAIL'}")

    if n_fail:
        print("\nFAILURES:")
        for s, name, detail in _RESULTS:
            if s == "FAIL":
                print(f"  - {name}: {detail}")
    if n_block:
        print("\nBLOCKED:")
        for s, name, detail in _RESULTS:
            if s == "BLOCKED":
                print(f"  - {name}: {detail}")

    if n_fail:
        print("\nRESULT: FAIL — see failures above.")
        return 1
    if n_block:
        print("\nRESULT: BLOCKED — a Task-1 gate did not pass (honest, expected "
              "outcome). Provision a real VOYAGE_API_KEY and re-run.")
        return 2
    print("\nRESULT: PASS — all assertions green.")
    return 0


if __name__ == "__main__":
    if not DATABASE_URL:
        print("[FATAL] DATABASE_URL not set — cannot run verify")
        sys.exit(1)
    ga = gb = False
    try:
        ga, gb = asyncio.run(main_async())
    except Exception:  # noqa: BLE001
        print("[FATAL] verify crashed:")
        traceback.print_exc()
        _RESULTS.append(("FAIL", "verify run", "crashed — see traceback"))
    sys.exit(summarize(ga, gb))
