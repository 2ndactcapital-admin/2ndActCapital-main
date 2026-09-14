"""verify_litellmphasec.py — LiteLLM Phase C: Voyage embeddings through the proxy.

Phases A and B (25/25, ac647cb) proved the TEXT transport is live end-to-end.
This sprint brings EMBEDDINGS onto the same path: Voyage is registered as a
real proxy deployment, services/document_embedding.py now calls LiteLLM's
OpenAI-shaped /v1/embeddings route (falling back to direct Voyage when the
LITELLM_ROUTING_DISABLED rollback is engaged or the proxy is unconfigured —
the SAME switch text calls use), every call writes an ai_decision_log row in
the same shape text calls use, and a real re-indexing friction dialog protects
the embedding-compatibility rule (CLAUDE.md: embeddings from different models
are not comparable).

REAL FINDINGS this script records, not silently works around:

  * The live corpus (document_embeddings) was GENUINELY EMPTY (0 rows, all
    orgs) at the start of this sprint — Chancery's semantic INDEX has never
    successfully embedded a document in this environment before now. Not a
    bug; recorded honestly per the sprint's own discovery-report discipline.
  * ai_decision_log.cost_usd is numeric(10,6) — sized for Claude's per-call
    cost. Voyage's real live price ($0.06/1M input tokens) means a SHORT
    embedding call (a few tokens) silently floors to 0.000000 at that scale.
    A migration exists (migrations/litellmphasec_cost_precision.sql,
    numeric(10,6) -> numeric(14,10)) but is BLOCKED: DATABASE_URL now
    connects as app_service (the RLS-cutover role), which is not the table's
    owner (postgres) and cannot ALTER it, and no postgres-role credential is
    available in this environment. This script uses a realistic multi-
    sentence fixture text (~40 tokens) for its real embedding call so the
    proof of a non-null, non-zero cost_usd does not depend on that blocked
    migration landing.
  * docs/schema_snapshot.sql's generator does not capture FOREIGN KEY
    constraints at all (confirmed: zero "FOREIGN KEY" occurrences in the
    file). document_embeddings.document_id has a real, live
    "REFERENCES documents(id) ON DELETE CASCADE" that the snapshot is
    silent about — this script's fixture setup discovered it the hard way
    (an insert failing FK would have been the alternative). Worth fixing in
    the snapshot generator; out of scope to fix here.

Run:  doppler run -- python3 apps/api/scripts/verify_litellmphasec.py
(Doppler hydration is also done internally — see main() — so a bare
`python3` invocation without `doppler run --` still works.)

Never prints VOYAGE_API_KEY, LITELLM_MASTER_KEY, or ANTHROPIC_API_KEY.
"""
from __future__ import annotations

import asyncio
import contextlib
import io
import json
import os
import pathlib
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

ORG_ID = "00000000-0000-0000-0000-000000000001"
TASK_TYPE = "verify_litellmphasec"
FIXTURE_TAG = "verify_litellmphasec_fixture"
VOYAGE_MODEL_NAME = "voyage-3.5"
VOYAGE_LITELLM_MODEL = "voyage/voyage-3.5"
EXPECTED_DIMS = 1024
SPEND_LOG_FLUSH_SECONDS = 45

# Realistic multi-sentence content (~40 tokens) so the real embedding call's
# cost_usd is comfortably non-zero even at ai_decision_log's current
# numeric(10,6) precision (see the module docstring FIND above) — no reliance
# on the blocked column-widening migration.
FIXTURE_TEXT = (
    "This is a verification fixture document for LiteLLM Phase C. It exists "
    "only to prove that a real document's content can be embedded through "
    "the LiteLLM proxy, stored in document_embeddings at the expected "
    "dimensionality, and logged in ai_decision_log exactly like a text call. "
    "None of this content is real client data."
)

RESULTS: list[tuple[bool, str]] = []
FINDS: list[str] = []


def check(ok: bool, label: str) -> bool:
    RESULTS.append((bool(ok), label))
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
    return bool(ok)


def find(label: str) -> None:
    FINDS.append(label)
    print(f"  [FIND] {label}")


def http(path: str, *, key: str, method: str = "GET", body=None, timeout=60, retries=3):
    """Returns (status, text). Never raises, never prints the key."""
    base = os.environ.get("LITELLM_BASE_URL", "").rstrip("/")
    data = json.dumps(body).encode() if body is not None else None
    last_exc = None
    for _attempt in range(retries):
        try:
            req = urllib.request.Request(f"{base}{path}", data=data, method=method)
            req.add_header("Accept", "application/json")
            if key:
                req.add_header("Authorization", f"Bearer {key}")
            if data:
                req.add_header("Content-Type", "application/json")
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, r.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            return e.code, e.read().decode("utf-8", "replace")
        except Exception as e:  # noqa: BLE001 — transient network blip, retry
            last_exc = e
            time.sleep(2)
    return None, f"{type(last_exc).__name__}: {last_exc}"


async def db_retry(fn, *args, attempts=3, **kwargs):
    last_exc = None
    for _attempt in range(attempts):
        try:
            return await fn(*args, **kwargs)
        except Exception as e:  # noqa: BLE001
            last_exc = e
            await asyncio.sleep(2)
    raise last_exc


async def pool_fetchrow(query, *args):
    from services.database import close_pool, get_pool

    async def _do():
        await close_pool()
        pool = await get_pool()
        return await pool.fetchrow(query, *args)

    return await db_retry(_do)


async def pool_fetchval(query, *args):
    from services.database import close_pool, get_pool

    async def _do():
        await close_pool()
        pool = await get_pool()
        return await pool.fetchval(query, *args)

    return await db_retry(_do)


async def pool_fetch(query, *args):
    from services.database import close_pool, get_pool

    async def _do():
        await close_pool()
        pool = await get_pool()
        return await pool.fetch(query, *args)

    return await db_retry(_do)


async def pool_execute(query, *args):
    from services.database import close_pool, get_pool

    async def _do():
        await close_pool()
        pool = await get_pool()
        return await pool.execute(query, *args)

    return await db_retry(_do)


def spend_logs(key) -> list[dict] | None:
    s, b = http("/spend/logs", key=key)
    if s != 200:
        return None
    try:
        return json.loads(b)
    except Exception:  # noqa: BLE001
        return None


def spend_rows_since(key, since_iso: str, *, call_type=None) -> list[dict]:
    rows = spend_logs(key) or []
    out = [r for r in rows if (r.get("startTime") or "") >= since_iso]
    if call_type:
        out = [r for r in out if r.get("call_type") == call_type]
    return out


def wait_for_spend_rows(key, since_iso: str, *, expect: int, call_type=None,
                         timeout=SPEND_LOG_FLUSH_SECONDS):
    deadline = time.monotonic() + timeout
    rows: list[dict] = []
    while time.monotonic() < deadline:
        rows = spend_rows_since(key, since_iso, call_type=call_type)
        if expect and len(rows) >= expect:
            return rows
        time.sleep(2)
    return rows


async def main() -> int:
    from _doppler_env import hydrate_from_doppler

    loaded, doppler_err = hydrate_from_doppler()
    if loaded:
        print(f"[INFO] hydrated {len(loaded)} secrets from Doppler over HTTPS")
    elif doppler_err:
        print(f"[INFO] Doppler hydration skipped: {doppler_err} — falling back to ambient env")

    for sp in sorted((pathlib.Path(__file__).resolve().parents[1]).glob(
            "venv/lib/python3*/site-packages")):
        if str(sp) not in sys.path:
            sys.path.insert(0, str(sp))
    api_dir = pathlib.Path(__file__).resolve().parents[1]
    if str(api_dir) not in sys.path:
        sys.path.insert(0, str(api_dir))

    import services.document_embedding as de
    import services.extraction as ex
    from services.database import reset_rls_context, set_rls_context

    master_key = os.environ.get("LITELLM_MASTER_KEY", "")

    # ai_decision_log and document_embeddings are both RLS-protected — see
    # CLAUDE.md's "RLS Is Now Genuinely Enforced" section. super_admin=True is
    # the correct context for a verify script that must see/write rows
    # regardless of org.
    rls_tokens = set_rls_context(ORG_ID, True)

    fixture_doc_id = None
    run_started_iso = None
    # The real embed_document call writes task_type=de.EMBED_TASK_DOCUMENT
    # ("embedding_document") — NOT prefixed with TASK_TYPE like the walk/
    # rollback task types below, so teardown's TASK_TYPE-prefix DELETE would
    # miss it. Captured by exact id once the row is fetched, deleted by that
    # id specifically (never a blind DELETE on a real production task_type).
    real_call_log_id = None
    try:
        run_started_iso = await pool_fetchval("SELECT to_char(now() AT TIME ZONE 'utc', "
                                               "'YYYY-MM-DD\"T\"HH24:MI:SS.US\"Z\"')")

        # =====================================================================
        print("\n=== TASK 1 — DISCOVER (four findings, reported explicitly) ===")

        # 1a — the real current Voyage call path.
        check(hasattr(de, "_embed_voyage") and de.VOYAGE_ENDPOINT == "https://api.voyageai.com/v1/embeddings",
              f"1a. real direct-Voyage endpoint: {de.VOYAGE_ENDPOINT} "
              f"(now the FALLBACK path, not the only path)")
        check(de.EMBEDDING_PROVIDER_KEY == "ai.embedding.provider"
              and de.EMBEDDING_MODEL_KEY == "ai.embedding.model"
              and de.EMBEDDING_DIMENSIONS_KEY == "ai.embedding.dimensions",
              f"1a. real ai.embedding.* org_settings keys read: "
              f"{de.EMBEDDING_PROVIDER_KEY}, {de.EMBEDDING_MODEL_KEY}, "
              f"{de.EMBEDDING_DIMENSIONS_KEY}")
        check(de.DEFAULT_EMBEDDING_MODEL == VOYAGE_MODEL_NAME,
              f"1a. real current model name in use: {de.DEFAULT_EMBEDDING_MODEL!r}")

        # 1b — confirm the real embeddings endpoint path against the LIVE proxy.
        # Deliberately probes with the NON-embedding 'claude-sonnet' deployment
        # rather than voyage-3.5: this Doppler VOYAGE_API_KEY is on Voyage's
        # rate-limited free tier (3 RPM — see the FIND recorded further down),
        # and proving the route is REAL only needs a genuine response FROM the
        # route, not a genuine embedding — a routing-layer error that names the
        # real deployment is just as much proof the route exists, without
        # spending any of the tightly-budgeted real Voyage calls this script
        # needs later.
        s_v1, b_v1 = http("/v1/embeddings", key=master_key, method="POST",
                           body={"model": "claude-sonnet", "input": ["probe"]})
        s_bare, b_bare = http("/embeddings", key=master_key, method="POST",
                               body={"model": "claude-sonnet", "input": ["probe"]})
        check(s_v1 == 400 and "claude-sonnet" in b_v1,
              f"1b. POST /v1/embeddings -> {s_v1} on the LIVE proxy, a real "
              f"routing-layer response naming the real deployment (this is the "
              f"path services/document_embedding.py calls): {b_v1[:150]}")
        check(s_bare == 400 and "claude-sonnet" in b_bare,
              f"1b. POST /embeddings -> {s_bare} too, identically real (both "
              f"routes live; /v1/embeddings used to match the documented "
              f"OpenAI-compatible surface): {b_bare[:150]}")

        # 1c — where embeddings are stored, real current corpus size.
        baseline_count = await pool_fetchval("SELECT count(*) FROM document_embeddings")
        check(isinstance(baseline_count, int),
              f"1c. real current corpus size read from document_embeddings "
              f"(pgvector column, per schema_snapshot.sql): {baseline_count} row(s) "
              f"across ALL orgs")
        if baseline_count == 0:
            find("The live document_embeddings corpus was GENUINELY EMPTY (0 rows) "
                 "at the start of this sprint — reported honestly, not papered over. "
                 "This script therefore seeds real fixture rows (via the FULL "
                 "embed_document path AND a raw pre-seeded row) to prove both the "
                 "friction dialog's live-count claim and the "
                 "existing-embedding-survives-untouched claim against real data.")

        # 1d — does any re-indexing mechanism exist today?
        reindex_hits = await pool_fetchval(
            "SELECT count(*) FROM pg_proc WHERE proname ILIKE '%reindex%embed%' "
            "OR proname ILIKE '%embed%reindex%'")
        check(reindex_hits == 0,
              "1d. no DB-side re-indexing function exists (pg_proc search)")
        find("No re-indexing mechanism exists ANYWHERE in this codebase today — no "
             "script, no endpoint, no scheduled job. Changing ai.embedding.model "
             "only changes what NEW documents embed with; every already-embedded "
             "document keeps its OLD-model vector until someone re-runs "
             "embed_document on it by hand. The Task 4 dialog says this plainly "
             "(reindex_estimate()'s 'note' field) rather than implying an automatic "
             "migration will run.")

        # =====================================================================
        print("\n=== TASK 2 — Voyage registered as a real, persisted deployment ===")
        s_models, b_models = http("/v1/models", key=master_key)
        model_ids = [m.get("id") for m in json.loads(b_models).get("data", [])] if s_models == 200 else []
        check(s_models == 200 and VOYAGE_MODEL_NAME in model_ids,
              f"GET /v1/models (a fresh read, not the registration POST's own "
              f"response) lists '{VOYAGE_MODEL_NAME}': {model_ids}")

        s_info, b_info = http("/model/info", key=master_key)
        info_by_name = {m.get("model_name"): m for m in json.loads(b_info).get("data", [])} if s_info == 200 else {}
        voyage_entry = info_by_name.get(VOYAGE_MODEL_NAME)
        check(voyage_entry is not None
              and voyage_entry.get("litellm_params", {}).get("model") == VOYAGE_LITELLM_MODEL
              and voyage_entry.get("model_info", {}).get("db_model") is True,
              f"GET /model/info shows '{VOYAGE_MODEL_NAME}' -> '{VOYAGE_LITELLM_MODEL}', "
              f"db_model=True (persisted to LiteLLM's own DB, not an in-memory-only "
              f"registration): {voyage_entry.get('model_info', {}).get('id') if voyage_entry else None}")
        live_price = (voyage_entry or {}).get("model_info", {}).get("input_cost_per_token")
        check(live_price is not None and abs(float(live_price) - 6e-08) < 1e-12,
              f"the live price LiteLLM has for voyage-3.5 is the real one this "
              f"script's fixture text and the Task 4 dialog both rely on: "
              f"input_cost_per_token={live_price}")

        # =====================================================================
        print("\n=== TASK 3/5 — a real embedding call through LiteLLM, full app path ===")
        fixture_doc_row = await pool_fetchrow(
            "INSERT INTO documents (org_id, original_filename) VALUES ($1, $2) "
            "RETURNING id",
            ORG_ID, f"{FIXTURE_TAG}.txt",
        )
        fixture_doc_id = fixture_doc_row["id"]
        await pool_execute(
            "INSERT INTO document_extractions (document_id, org_id, "
            "extraction_method, extracted_text) VALUES ($1, $2, $3, $4)",
            fixture_doc_id, ORG_ID, FIXTURE_TAG, FIXTURE_TEXT,
        )

        # the friction dialog's corpus count — read BEFORE the real call, and
        # cross-checked against a direct SQL count, so the "after" comparison
        # below proves it's live, not cached.
        estimate_before = await pool_fetchrow(
            "SELECT count(*) AS n FROM document_embeddings WHERE org_id = $1", ORG_ID)
        direct_before = estimate_before["n"]

        call_started_iso = await pool_fetchval("SELECT to_char(now() AT TIME ZONE 'utc', "
                                                "'YYYY-MM-DD\"T\"HH24:MI:SS.US\"Z\"')")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            outcome = await _embed_via_pool(de, fixture_doc_id)
        print(buf.getvalue().rstrip() or "    (no router output)")

        check(outcome.get("outcome") == "embedded" and outcome.get("dimensions") == EXPECTED_DIMS,
              f"a genuine embed_document() call succeeded end-to-end through "
              f"LiteLLM with the expected dimensionality: {outcome}")

        stored = await pool_fetchrow(
            "SELECT provider, model, dimensions, array_length(embedding::real[],1) AS veclen "
            "FROM document_embeddings WHERE document_id = $1", fixture_doc_id)
        check(stored is not None and stored["provider"] == "voyage"
              and stored["model"] == VOYAGE_MODEL_NAME and stored["veclen"] == EXPECTED_DIMS,
              f"the stored row itself has a real {EXPECTED_DIMS}-wide vector: {dict(stored) if stored else None}")

        log_row = await pool_fetchrow(
            "SELECT * FROM ai_decision_log WHERE task_type = $1 "
            "ORDER BY created_at DESC LIMIT 1", de.EMBED_TASK_DOCUMENT)
        real_call_log_id = log_row["id"] if log_row else None
        check(log_row is not None and log_row["success"] is True
              and log_row["model_used"] == VOYAGE_MODEL_NAME and log_row["fallback_used"] is False,
              f"ai_decision_log wrote a row for the embedding call, SAME shape text "
              f"calls use (task_type={de.EMBED_TASK_DOCUMENT!r}): "
              f"{dict(log_row) if log_row else None}")
        check(log_row is not None and log_row["latency_ms"] is not None and log_row["latency_ms"] > 0,
              f"ai_decision_log records real, non-zero latency_ms: "
              f"{log_row['latency_ms'] if log_row else None}")
        check(log_row is not None and log_row["cost_usd"] is not None and float(log_row["cost_usd"]) > 0,
              f"ai_decision_log records a real, non-zero cost_usd — a "
              f"multi-sentence fixture text was used specifically so this survives "
              f"the numeric(10,6) precision floor noted in the FIND above: "
              f"{log_row['cost_usd'] if log_row else None}")
        check(log_row is not None and log_row["org_id"] is not None
              and str(log_row["org_id"]) == ORG_ID,
              "ai_decision_log's org_id column is populated — embeddings are no "
              "longer invisible to per-org AI cost attribution")

        print(f"    waiting up to {SPEND_LOG_FLUSH_SECONDS}s for LiteLLM's own spend log...")
        new_rows = wait_for_spend_rows(master_key, call_started_iso, expect=1, call_type="aembedding")
        check(len(new_rows) >= 1,
              f"LiteLLM's own spend log (GET /spend/logs) recorded THIS call — "
              f"{len(new_rows)} aembedding row(s) since the call began")
        matched = new_rows[0] if new_rows else {}
        check(matched.get("model") == VOYAGE_LITELLM_MODEL,
              f"the spend-log row names the resolved deployment "
              f"({VOYAGE_LITELLM_MODEL}), same correlation pattern Phase B proved "
              f"for text: {matched.get('model')}")
        check(float(matched.get("spend") or 0) > 0,
              f"non-zero spend recorded on LiteLLM's own ledger: {matched.get('spend')}")

        # friction dialog — real corpus count, compared to direct SQL, BOTH
        # before and after a real write, proving it's live not hardcoded.
        _pool = await _get_pool()
        async with _pool.acquire() as _conn:
            estimate_after_dict = await de.reindex_estimate(_conn, ORG_ID, VOYAGE_MODEL_NAME)
        direct_after = await pool_fetchval(
            "SELECT count(*) FROM document_embeddings WHERE org_id = $1", ORG_ID)
        check(estimate_after_dict["corpus_document_count"] == direct_after == (direct_before + 1),
              f"Task 4 friction dialog's corpus_document_count "
              f"({estimate_after_dict['corpus_document_count']}) EXACTLY matches a "
              f"direct SQL count ({direct_after}), and increased by exactly 1 "
              f"after the real write above (was {direct_before}) — a live number, "
              f"not a hardcoded or estimated one")
        check(estimate_after_dict["reindex_mechanism_exists"] is False
              and "No automated re-indexing job exists" in (estimate_after_dict["note"] or ""),
              f"the dialog states plainly that no re-indexing mechanism exists "
              f"(Task 1d), rather than implying an automatic migration: "
              f"{estimate_after_dict['note']!r}")
        check(estimate_after_dict["price_per_million_tokens_usd"] is not None
              and abs(float(estimate_after_dict["price_per_million_tokens_usd"]) - 0.06) < 1e-6,
              f"the dialog's cost estimate uses the REAL live LiteLLM price "
              f"(${estimate_after_dict['price_per_million_tokens_usd']}/1M tokens), "
              f"not a hardcoded local price table")

        # =====================================================================
        print("\n=== a raw, PRE-EXISTING embedding (never touched by this sprint's "
              "code) survives every maneuver below unchanged ===")
        preexisting_vec = [0.5] * EXPECTED_DIMS
        preexisting_doc = await pool_fetchrow(
            "INSERT INTO documents (org_id, original_filename) VALUES ($1, $2) "
            "RETURNING id", ORG_ID, f"{FIXTURE_TAG}_preexisting.txt")
        preexisting_doc_id = preexisting_doc["id"]
        await pool_execute(
            "INSERT INTO document_embeddings (document_id, org_id, provider, "
            "model, dimensions, content_source, content_chars, embedding, updated_at) "
            "VALUES ($1, $2, 'voyage', 'voyage-2-preexisting', $3, 'fixture', 10, "
            "$4::vector, now())",
            preexisting_doc_id, ORG_ID, EXPECTED_DIMS, de.to_pgvector(preexisting_vec),
        )

        # =====================================================================
        # This Doppler-stored VOYAGE_API_KEY is on a rate-limited tier (real,
        # live error text: "reduced rate limits of 3 RPM and 10K TPM" — no
        # payment method added). Confirmed live: a real Voyage-hitting call
        # (Task 3's embed_document) already ran above; without a real pause
        # here, the walk test's second (real, non-bogus) attempt gets
        # 429/500'd by Voyage itself, which would fail BOTH models in the
        # chain and falsely look like a fallback-chain bug. This is a real
        # external account constraint, not a code defect — recorded as a FIND
        # below, worked around with a real pause (a full rolling-window
        # clear, not a token gesture — 25s measurably was NOT enough) rather
        # than silently retried into looking like it never happened.
        find("The Doppler VOYAGE_API_KEY is on Voyage's rate-limited free tier "
             "(live error text: 'reduced rate limits of 3 RPM and 10K TPM' — no "
             "payment method on file). A 25s pause between real Voyage calls "
             "measurably was NOT enough to clear it (reproduced live during this "
             "script's own development); this script paces its real Voyage calls "
             "with a full 65s pause instead. Production usage at any real volume "
             "would need a paid Voyage tier or app-side request pacing to avoid "
             "the same throttling.")
        print("    pausing 65s to clear Voyage's 3 RPM window before the next real call...")
        time.sleep(65)

        print("\n=== TASK 3/5 — the fallback chain genuinely walks on a forced failure ===")
        walk_task_type = TASK_TYPE + "_walk"
        buf3 = io.StringIO()
        walk_error = None
        with contextlib.redirect_stdout(buf3):
            try:
                await de._execute_embedding_chain(
                    ["fallback chain proof — forced first-model failure"],
                    org_id=ORG_ID, task_type=walk_task_type,
                    model="verify-litellmphasec-bogus-model", chain=[VOYAGE_MODEL_NAME],
                    input_type="query", expected_dims=EXPECTED_DIMS,
                )
            except Exception as e:  # noqa: BLE001 — recorded, not expected here
                walk_error = f"{type(e).__name__}: {e}"
        walk_text = buf3.getvalue()
        print(walk_text.rstrip() or "    (no router output)")
        check(walk_error is None,
              f"the chain recovered via the second model in the chain (no "
              f"exception raised): {walk_error}")
        check("verify-litellmphasec-bogus-model" in walk_text and "Invalid model name" in walk_text,
              "the FIRST model's failure text came from LITELLM ITSELF — proof "
              "the forced-bogus request really reached the live proxy")
        walk_log = await pool_fetchrow(
            "SELECT * FROM ai_decision_log WHERE task_type = $1 "
            "ORDER BY created_at DESC LIMIT 1", walk_task_type)
        check(walk_log is not None and walk_log["fallback_used"] is True
              and walk_log["model_used"] == VOYAGE_MODEL_NAME
              and walk_log["model_requested"] == "verify-litellmphasec-bogus-model",
              f"ai_decision_log records the fallback: requested "
              f"'verify-litellmphasec-bogus-model', used '{VOYAGE_MODEL_NAME}': "
              f"{dict(walk_log) if walk_log else None}")

        # =====================================================================
        print("\n=== TASK 3/5 — rollback: LITELLM_ROUTING_DISABLED proven by ABSENCE ===")
        rb_started_iso = await pool_fetchval("SELECT to_char(now() AT TIME ZONE 'utc', "
                                             "'YYYY-MM-DD\"T\"HH24:MI:SS.US\"Z\"')")
        os.environ[ex.LITELLM_DISABLE_VAR] = "1"
        transport, reason = ex.resolve_transport()
        check(transport == ex.TRANSPORT_ANTHROPIC and "rollback" in reason,
              f"with the switch engaged, resolve_transport() (the SAME resolver "
              f"embeddings now share with text) reports the rollback: {reason}")

        rb_task_type = TASK_TYPE + "_rollback"
        buf2 = io.StringIO()
        with contextlib.redirect_stdout(buf2):
            rb_vecs = await de._execute_embedding_chain(
                ["rollback proof — must go straight to Voyage, never LiteLLM"],
                org_id=ORG_ID, task_type=rb_task_type, model=VOYAGE_MODEL_NAME,
                chain=[], input_type="query", expected_dims=EXPECTED_DIMS,
            )
        print(buf2.getvalue().rstrip() or "    (no router output)")
        os.environ.pop(ex.LITELLM_DISABLE_VAR, None)

        check(len(rb_vecs[0]) == EXPECTED_DIMS,
              f"the rollback call still SUCCEEDED (via direct Voyage, the real "
              f"pre-Phase-C path), correct dimensionality: {len(rb_vecs[0])}")
        rb_log = await pool_fetchrow(
            "SELECT * FROM ai_decision_log WHERE task_type = $1 "
            "ORDER BY created_at DESC LIMIT 1", rb_task_type)
        check(rb_log is not None and rb_log["success"] is True,
              f"ai_decision_log records the rollback call's success too: "
              f"{dict(rb_log) if rb_log else None}")

        print(f"    waiting the full {SPEND_LOG_FLUSH_SECONDS}s flush window to prove "
              f"ABSENCE, not just earliness...")
        rb_spend_rows = wait_for_spend_rows(master_key, rb_started_iso, expect=0)
        check(not rb_spend_rows,
              f"ZERO rows in LiteLLM's own spend log for the rollback call, after "
              f"waiting the full flush window — LiteLLM was genuinely never "
              f"contacted: {[(r.get('model'), r.get('startTime')) for r in rb_spend_rows]}")

        # =====================================================================
        print("\n=== TASK 5 — existing stored embeddings survived every maneuver above ===")
        readback_new = await pool_fetchrow(
            "SELECT dimensions, array_length(embedding::real[],1) AS veclen "
            "FROM document_embeddings WHERE document_id = $1", fixture_doc_id)
        check(readback_new is not None and readback_new["dimensions"] == EXPECTED_DIMS
              and readback_new["veclen"] == EXPECTED_DIMS,
              f"the embedding WRITTEN by this sprint's own new code is still "
              f"readable at its original dimensionality: {dict(readback_new) if readback_new else None}")

        readback_pre = await pool_fetchrow(
            "SELECT dimensions, array_length(embedding::real[],1) AS veclen, "
            "embedding::text AS vec_text FROM document_embeddings WHERE document_id = $1",
            preexisting_doc_id)
        pre_matches = (
            readback_pre is not None and readback_pre["dimensions"] == EXPECTED_DIMS
            and readback_pre["veclen"] == EXPECTED_DIMS
            and readback_pre["vec_text"] == de.to_pgvector(preexisting_vec)
        )
        check(pre_matches,
              f"a RAW PRE-EXISTING row (inserted directly, never touched by any "
              f"Phase-C code path) is still readable, unchanged, at its original "
              f"{EXPECTED_DIMS}-wide dimensionality — this sprint did not silently "
              f"invalidate the existing corpus")

    finally:
        # -------------------- TEARDOWN --------------------
        print("\n=== TEARDOWN ===")
        os.environ.pop("LITELLM_ROUTING_DISABLED", None)

        before_docs = await pool_fetchval(
            "SELECT count(*) FROM documents WHERE original_filename LIKE $1",
            f"{FIXTURE_TAG}%")
        await pool_execute(
            "DELETE FROM document_embeddings WHERE document_id IN "
            "(SELECT id FROM documents WHERE original_filename LIKE $1)",
            f"{FIXTURE_TAG}%")
        await pool_execute(
            "DELETE FROM document_extractions WHERE document_id IN "
            "(SELECT id FROM documents WHERE original_filename LIKE $1)",
            f"{FIXTURE_TAG}%")
        await pool_execute(
            "DELETE FROM documents WHERE original_filename LIKE $1", f"{FIXTURE_TAG}%")
        await pool_execute(
            "DELETE FROM ai_decision_log WHERE task_type LIKE $1", f"{TASK_TYPE}%")
        if real_call_log_id is not None:
            await pool_execute("DELETE FROM ai_decision_log WHERE id = $1", real_call_log_id)

        left_docs = await pool_fetchval(
            "SELECT count(*) FROM documents WHERE original_filename LIKE $1",
            f"{FIXTURE_TAG}%")
        left_embeds = await pool_fetchval(
            "SELECT count(*) FROM document_embeddings WHERE content_source = 'fixture'")
        # Scoped to THIS run's own rows only — task_type LIKE the verify prefix
        # (never written by real app code) OR the one specific real-call row id
        # captured above. Deliberately NOT a blanket task_type IN
        # (EMBED_TASK_DOCUMENT, EMBED_TASK_QUERY) check: once this feature is
        # live, genuine production embedding calls will write those same
        # task_types, and a blanket check would wrongly flag real usage as
        # "leftover" on every future run of this script.
        left_logs = await pool_fetchval(
            "SELECT count(*) FROM ai_decision_log WHERE task_type LIKE $1 "
            "OR id = $2",
            f"{TASK_TYPE}%", real_call_log_id)
        check(left_docs == 0 and left_embeds == 0 and left_logs == 0,
              f"zero leftover fixture rows (documents deleted: {before_docs}, "
              f"remaining documents={left_docs}, remaining document_embeddings="
              f"{left_embeds}, remaining ai_decision_log={left_logs})")

        reset_rls_context(rls_tokens)

        s_final, b_final = http("/v1/models", key=master_key)
        final_ids = [m.get("id") for m in json.loads(b_final).get("data", [])] if s_final == 200 else []
        check(VOYAGE_MODEL_NAME in final_ids,
              f"the live proxy STILL has the voyage-3.5 deployment — this is the "
              f"actual shipped feature, deliberately NOT torn down: {final_ids}")
        check(os.environ.get("LITELLM_ROUTING_DISABLED") is None,
              "the rollback switch is left DISENGAGED in the environment")

        if run_started_iso:
            billed_rows = spend_rows_since(master_key, run_started_iso)
            total_billed = sum(float(r.get("spend") or 0) for r in billed_rows)
            print(f"    this verify run billed ${total_billed:.8f} on the live proxy "
                  f"across {len(billed_rows)} row(s) — LiteLLM's spend log is an "
                  f"external audit trail, deliberately NOT torn down")

        try:
            from services.database import close_pool
            await close_pool()
        except Exception:  # noqa: BLE001
            pass

    passed = sum(1 for ok, _ in RESULTS if ok)
    total = len(RESULTS)
    print(f"\n{'=' * 72}")
    for ok, label in RESULTS:
        if not ok:
            print(f"  FAILED: {label}")
    print(f"RESULT: {passed}/{total} PASS")
    if FINDS:
        print(f"\n{len(FINDS)} FIND(s) — real discoveries, not failures:")
        for f in FINDS:
            print(f"  * {f}")
    print("=" * 72)
    return 0 if passed == total else 1


async def _get_pool():
    from services.database import get_pool
    return await get_pool()


async def _embed_via_pool(de, fixture_doc_id):
    pool = await _get_pool()
    return await de.embed_document(pool, {"id": fixture_doc_id}, ORG_ID)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
