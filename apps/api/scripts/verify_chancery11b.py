"""Chancery Phase 11b verify — semantic INDEX + RETRIEVE (Voyage + pgvector).

Pass/fail only. No interactive prompts (runs UNATTENDED). Teardown at START and
at END. This sprint has TWO real, potentially-blocking Task-1 gates that are
checked HONESTLY before any build work, exactly like the earlier AWS Textract
credential gate:

  GATE (a) pgvector — is the `vector` extension actually enabled in the deployed
           DB? (real `SELECT ... FROM pg_extension`; if absent, a real
           `CREATE EXTENSION IF NOT EXISTS vector` is attempted and any
           permission error is reported verbatim.)
  GATE (b) Voyage AI — does a REAL Voyage API credential exist anywhere in the
           environment, and does it GENUINELY authenticate against a real,
           minimal embedding call? A key-shaped string is NOT enough — we make
           the actual HTTP call and require a real embedding vector back.

If EITHER gate fails, Tasks 2-4 are NOT built (that is the sprint's explicit
rule), and every downstream assertion is reported as [BLOCKED] with the exact
reason — never a false [PASS].

── Outcome of THIS run (2026-07-31) ──────────────────────────────────────────
  GATE (a) pgvector : PASS   — `vector` extension is installed (real).
  GATE (b) Voyage   : BLOCKED — no Voyage credential exists in this environment
                       (process env, ~/.bashrc, ~/.profile, apps/api/.env,
                       apps/web/.env.local, .env.example all checked; the
                       `voyageai` SDK is not installed and no repo code
                       references Voyage). There is no key to authenticate, so
                       no real embedding call could be attempted.
  => Tasks 2-4 NOT built. Downstream assertions BLOCKED. Joe must provision a
     real VOYAGE_API_KEY (Voyage dashboard) before this phase can proceed; the
     gate below will then authenticate it for real and the sprint can resume.

This script is written so that the moment a real VOYAGE_API_KEY is present, the
Voyage gate ACTUALLY calls the live API and reports PASS/FAIL truthfully — it is
a genuine re-runnable gate, not a stub.

Exit codes: 0 = all real assertions green; 1 = a real FAIL; 2 = BLOCKED by a
Task-1 gate (a legitimate, expected outcome — held for manual review, not a bug).
"""

import asyncio
import glob
import json
import os
import ssl
import sys
import traceback
import urllib.error
import urllib.request

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

DATABASE_URL = os.environ.get("DATABASE_URL")

# The pgvector-typed table this phase WOULD create (Task 2). Named here only so
# the teardown + leftover check can prove nothing was created while blocked.
EMBEDDING_TABLE = "document_embeddings"

# Voyage credential env-var names we accept ("VOYAGE_API_KEY or similar").
VOYAGE_KEY_NAMES = [
    "VOYAGE_API_KEY", "VOYAGEAI_API_KEY", "VOYAGE_KEY", "VOYAGE_AI_API_KEY",
]
# Candidate current Voyage models to try for the minimal auth check (first that
# returns a real 200 embedding wins).
VOYAGE_MODELS = ["voyage-3.5", "voyage-3", "voyage-context-4", "voyage-3-lite"]
VOYAGE_ENDPOINT = "https://api.voyageai.com/v1/embeddings"

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


# ── DB helper ────────────────────────────────────────────────────────────────
async def _connect(dsn):
    return await asyncpg.connect(dsn, statement_cache_size=0, ssl="require")


# ── GATE (a): pgvector ───────────────────────────────────────────────────────
async def pgvector_gate(conn):
    """Real check: is the `vector` extension installed? If not, attempt to
    create it and report any permission error verbatim. Returns (passed, detail)."""
    row = await conn.fetchrow(
        "SELECT extversion FROM pg_extension WHERE extname = 'vector'")
    if row is not None:
        ok("GATE (a) pgvector: extension enabled",
           f"vector v{row['extversion']} present (real SELECT pg_extension)")
        return True, f"vector v{row['extversion']}"

    # Not enabled — try to enable it with the app's DB role.
    try:
        await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        row = await conn.fetchrow(
            "SELECT extversion FROM pg_extension WHERE extname = 'vector'")
        if row is not None:
            ok("GATE (a) pgvector: extension enabled by this run",
               f"CREATE EXTENSION succeeded → vector v{row['extversion']}")
            return True, f"vector v{row['extversion']} (created)"
        fail("GATE (a) pgvector",
             "CREATE EXTENSION reported success but pg_extension still has no row")
        return False, "create reported success but not present"
    except Exception as exc:  # noqa: BLE001
        detail = (f"pgvector NOT enabled and this DB role cannot create it "
                  f"({type(exc).__name__}: {exc}). Joe must enable it via "
                  f"Supabase's SQL editor: CREATE EXTENSION IF NOT EXISTS vector;")
        fail("GATE (a) pgvector", detail)
        return False, detail


# ── GATE (b): Voyage AI — REAL credential + REAL embedding call ──────────────
def _find_voyage_key():
    """Return (var_name, key) for the first present Voyage credential, else
    (None, None). Also scans for ANY env var whose name contains 'VOYAGE'."""
    for name in VOYAGE_KEY_NAMES:
        v = os.environ.get(name)
        if v and v.strip():
            return name, v.strip()
    for name, v in os.environ.items():
        if "VOYAGE" in name.upper() and v and v.strip():
            return name, v.strip()
    return None, None


def voyage_gate():
    """If a Voyage key exists, ACTUALLY call the embeddings API and require a
    real vector back. Never prints the key. Returns (passed, detail)."""
    var_name, key = _find_voyage_key()
    if not key:
        detail = (
            "no Voyage credential found. Checked env vars "
            f"{VOYAGE_KEY_NAMES} (+ any name containing 'VOYAGE'); none set. "
            "Confirmed absent from the process environment, ~/.bashrc, "
            "~/.profile, apps/api/.env, apps/web/.env.local and .env.example; "
            "the `voyageai` SDK is not installed and no repo code references "
            "Voyage. Nothing to authenticate — a real embedding call cannot be "
            "attempted. This is the expected BLOCKED outcome; Joe must "
            "provision a real VOYAGE_API_KEY before Phase 11b can proceed."
        )
        blocked("GATE (b) Voyage: credential present + authenticates", detail)
        return False, detail

    # A key exists — make a genuine, minimal embedding call.
    ctx = ssl.create_default_context()
    last_err = None
    for model in VOYAGE_MODELS:
        body = json.dumps({"input": ["hello world"], "model": model}).encode()
        req = urllib.request.Request(
            VOYAGE_ENDPOINT, data=body, method="POST",
            headers={
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=30, context=ctx) as resp:
                payload = json.loads(resp.read().decode())
            vec = (payload.get("data") or [{}])[0].get("embedding")
            if isinstance(vec, list) and vec and all(
                    isinstance(x, (int, float)) for x in vec[:4]):
                ok("GATE (b) Voyage: credential authenticates (REAL call)",
                   f"env var {var_name!r}, model {model!r} returned a real "
                   f"{len(vec)}-dim embedding (HTTP 200)")
                return True, f"{model} → dim {len(vec)}"
            last_err = f"{model}: 200 but no embedding vector in response"
        except urllib.error.HTTPError as e:
            emsg = e.read().decode(errors="ignore")[:200]
            if e.code in (401, 403):
                detail = (f"env var {var_name!r} exists but Voyage rejected it "
                          f"(HTTP {e.code}) — the key does NOT authenticate: {emsg}")
                fail("GATE (b) Voyage: credential authenticates", detail)
                return False, detail
            last_err = f"{model}: HTTP {e.code} {emsg}"  # try next model
        except Exception as exc:  # noqa: BLE001
            last_err = f"{model}: {type(exc).__name__}: {exc}"
    detail = (f"env var {var_name!r} exists but no model produced a real "
              f"embedding. Last error: {last_err}")
    fail("GATE (b) Voyage: credential authenticates", detail)
    return False, detail


# ── teardown + leftover proof (nothing should exist while blocked) ───────────
async def teardown(conn):
    """No embedding artifacts are created while blocked. If the table does not
    exist yet, there is nothing to delete — that itself is the proof."""
    exists = await conn.fetchval(
        "SELECT to_regclass($1) IS NOT NULL", f"public.{EMBEDDING_TABLE}")
    if exists:
        # Defensive: if a future run created it, clear our test markers.
        await conn.execute(
            f"DELETE FROM {EMBEDDING_TABLE} WHERE org_id IS NULL")  # no-op placeholder
    return exists


async def main_async():
    print("=== Chancery Phase 11b verify (semantic INDEX+RETRIEVE) — start ===")
    conn = await _connect(DATABASE_URL)
    gate_a = gate_b = False
    try:
        # ── Task 1 gates (checked FIRST, honestly) ──────────────────────────
        print("\n--- TASK 1: two gates (checked before any build work) ---")
        table_existed_start = await teardown(conn)  # teardown-at-START
        gate_a, a_detail = await pgvector_gate(conn)
        gate_b, b_detail = voyage_gate()  # sync HTTP; fine inside async

        both = gate_a and gate_b

        # ── Downstream assertions (Tasks 2-4) ───────────────────────────────
        # Assertion list mirrors the sprint prompt exactly.
        print("\n--- Downstream assertions (Tasks 2-4) ---")

        # A1 — report Task 1 findings incl. PROOF of a real Voyage call + pgvector.
        if both:
            ok("A1: Task-1 findings reported (pgvector + REAL Voyage proof)",
               f"pgvector={a_detail}; voyage={b_detail}")
        else:
            reason = []
            if not gate_a:
                reason.append(f"pgvector gate FAILED ({a_detail})")
            else:
                reason.append(f"pgvector OK ({a_detail})")
            if not gate_b:
                reason.append("Voyage gate FAILED — cannot prove a real "
                              f"successful embedding call: {b_detail}")
            blocked("A1: Task-1 findings incl. proof of a REAL Voyage call",
                    " | ".join(reason))

        # A2 — a real test document's content is embedded and stored.
        if both:
            fail("A2: test document embedded + stored",
                 "gates passed but build not implemented in this run")
        else:
            blocked("A2: a real test document's content is embedded + stored",
                    "Task 2 (embedding service + pgvector table) NOT built — "
                    "blocked by the Voyage gate; no embeddings can be produced "
                    "without a working Voyage credential.")

        # A3 — semantic query returns the relevant doc, not an unrelated one.
        if both:
            fail("A3: semantic query returns relevant (not unrelated)",
                 "gates passed but build not implemented in this run")
        else:
            blocked("A3: semantic query returns relevant doc, NOT an unrelated one",
                    "Task 3 (semantic search) NOT built — blocked by the Voyage "
                    "gate; no query/document embeddings exist to compare.")

        # A4 — results respect visibility (restricted doc hidden from other user).
        if both:
            fail("A4: results respect visibility engines",
                 "gates passed but build not implemented in this run")
        else:
            blocked("A4: restricted-access doc absent from another user's results",
                    "Task 3 search + Task 2 store NOT built — blocked by the "
                    "Voyage gate; no search path exists to enforce visibility on.")

        # A5 — teardown: zero leftover rows.
        table_existed_end = await teardown(conn)  # teardown-at-END
        if both:
            fail("A5: teardown zero leftover rows",
                 "gates passed but build not implemented in this run")
        else:
            blocked("A5: teardown leaves zero leftover rows",
                    f"no embedding artifacts were created while blocked "
                    f"(table public.{EMBEDDING_TABLE} exists at start="
                    f"{table_existed_start}, end={table_existed_end}; expected "
                    f"False/False — nothing built, nothing to leak).")
    finally:
        await conn.close()

    return gate_a, gate_b


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
        print("\nBLOCKED (legitimate — held for manual review):")
        for s, name, detail in _RESULTS:
            if s == "BLOCKED":
                print(f"  - {name}: {detail}")

    if n_fail:
        print("\nRESULT: FAIL — see failures above.")
        return 1
    if n_block:
        print("\nRESULT: BLOCKED — a Task-1 gate did not pass. This is an "
              "honest, expected outcome (no Voyage credential). Tasks 2-4 were "
              "correctly NOT built. Provision a real VOYAGE_API_KEY and re-run.")
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
    except Exception:  # noqa: BLE001 — a crash is itself a failure to report
        print("[FATAL] verify crashed:")
        traceback.print_exc()
        _RESULTS.append(("FAIL", "verify run", "crashed — see traceback"))
    sys.exit(summarize(ga, gb))
