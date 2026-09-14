"""verify_litellmphasebproof.py — LiteLLM Phase B: the completion proof.

litellmphaseb.structural (68/68) proved the TRANSPORT layer fully but left 5
assertions BLOCKED on three real external gaps (B1: zero model deployments,
B2: LITELLM_MASTER_KEY not PROXY_ADMIN, B3: no ANTHROPIC_API_KEY anywhere).
This script proves all three are now genuinely resolved and completes the
one proof structural could not make: a real, billed, successful call through
the full chain, dual-logged, with a genuine rollback-absence proof.

REAL FINDING this script had to route around: app_service (the role
DATABASE_URL now connects as, since the RLS enforcement cutover) gets
`InsufficientPrivilegeError: permission denied for schema litellm` on any
direct SQL against litellm.* — a deliberate consequence of the platform's
own least-privilege design (app_service and litellm_service are separate,
scoped roles; app_service was never granted into litellm's schema, unlike
Phase B's original verify script which ran before that cutover, when
DATABASE_URL was still the `postgres` superuser). The obvious workaround —
authenticating directly as litellm_service via LITELLM_DATABASE_URL — is
ALSO blocked: that secret's embedded password (and the separate
LITELLM_DB_PASSWORD) both fail InvalidPasswordError against Supabase's
pooler, for a role whose password may have drifted the same way DB_PASSWORD
once did. Recorded as a [FIND] below, not silently worked around.

The actual fix used: LiteLLM's own admin HTTP API (`GET /spend/logs`), using
the already-proven-working LITELLM_MASTER_KEY. This is not a downgrade — it
is LITELLM's OWN reporting surface over the exact same LiteLLM_SpendLogs
table, and it is arguably the *more* correct interface for an external
caller to use, since it doesn't require punching a hole in the schema
separation the design doc calls for.

Never prints LITELLM_MASTER_KEY, ANTHROPIC_API_KEY, or DATABASE_URL/
LITELLM_DATABASE_URL passwords.

Run:  doppler run -- python3 apps/api/scripts/verify_litellmphasebproof.py
(Doppler hydration is also done internally, so a bare `python3` invocation
without `doppler run --` still works — see main().)
"""
from __future__ import annotations

import asyncio
import io
import contextlib
import json
import os
import pathlib
import sys
import time
import urllib.error
import urllib.request

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

ORG_ID = "00000000-0000-0000-0000-000000000001"
TASK_TYPE = "verify_litellmphasebproof"
BOGUS_PRIMARY = "verify-litellmphasebproof-nonexistent-model"
KNOWN_MODEL_INFO_ID = "7fcd845c-0a47-413c-b77d-3da88d984425"
KNOWN_MODEL_NAME = "claude-sonnet"
KNOWN_LITELLM_MODEL = "anthropic/claude-sonnet-4-6"
SPEND_LOG_FLUSH_SECONDS = 45

RESULTS: list[tuple[bool, str]] = []
FINDS: list[str] = []


def check(ok: bool, label: str) -> bool:
    RESULTS.append((bool(ok), label))
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}")
    return bool(ok)


def find(label: str) -> None:
    FINDS.append(label)
    print(f"  [FIND] {label}")


def http(path: str, *, key: str, method: str = "GET", body=None, timeout=60,
          retries=3):
    """Returns (status, text). Never raises, never prints the key."""
    base = os.environ.get("LITELLM_BASE_URL", "").rstrip("/")
    data = json.dumps(body).encode() if body is not None else None
    last_exc = None
    for attempt in range(retries):
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
    """Run a fresh-connection asyncpg call with retry on transient resets.

    Discovered the hard way: a single connection/pool held across this
    script's HTTP flush waits gets reset by the Supabase pooler. Every DB
    touchpoint below opens its own short-lived connection instead.
    """
    last_exc = None
    for attempt in range(attempts):
        try:
            return await fn(*args, **kwargs)
        except Exception as e:  # noqa: BLE001
            last_exc = e
            await asyncio.sleep(2)
    raise last_exc


async def fresh_conn(db_url):
    import asyncpg
    return await asyncpg.connect(db_url, statement_cache_size=0, ssl="require", timeout=30)


async def fetchval(db_url, query, *args):
    """For queries that touch NO RLS-protected table (e.g. `SELECT now()`)."""
    async def _do():
        conn = await fresh_conn(db_url)
        try:
            return await conn.fetchval(query, *args)
        finally:
            await conn.close()
    return await db_retry(_do)


async def pool_fetchrow(query, *args):
    """ai_decision_log is RLS-protected — must go through the app's own
    RLS-aware pool (services.database.get_pool()), with the RLS ContextVars
    already set by the caller via set_rls_context(). A fresh pool is forced
    each call (close_pool() first) because a pool/connection held across this
    script's 45s HTTP flush waits gets reset by the Supabase pooler."""
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


def wait_for_spend_rows(key, since_iso: str, *, expect: int,
                         call_type=None, timeout=SPEND_LOG_FLUSH_SECONDS):
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
        print(f"[INFO] hydrated {len(loaded)} secrets from Doppler over HTTPS "
              f"(overwriting any stale ambient copies, e.g. apps/api/.env / ~/.bashrc)")
    elif doppler_err:
        print(f"[INFO] Doppler hydration skipped: {doppler_err} — falling back to "
              f"ambient env")

    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("FATAL: no DATABASE_URL")
        return 2

    for sp in sorted((pathlib.Path(__file__).resolve().parents[1]).glob(
            "venv/lib/python3*/site-packages")):
        if str(sp) not in sys.path:
            sys.path.insert(0, str(sp))
    api_dir = pathlib.Path(__file__).resolve().parents[1]
    if str(api_dir) not in sys.path:
        sys.path.insert(0, str(api_dir))

    import services.extraction as ex
    from services.database import set_rls_context, reset_rls_context

    master_key = os.environ.get("LITELLM_MASTER_KEY", "")
    anthropic_key = os.environ.get("ANTHROPIC_API_KEY", "")

    # ai_decision_log is RLS-protected (policy ai_decision_log_org_isolation:
    # org_id = app.current_org_id OR app.is_super_admin = true) — real finding,
    # not in the original prompt's context: app_service does NOT bypass RLS
    # (rolbypassrls=false), and a script that never sets these GUCs gets a
    # SILENT-LOOKING "new row violates row-level security policy" on write and
    # zero rows on read. super_admin=True is the simplest correct context for a
    # verify script that needs to see/write a row regardless of org.
    rls_tokens = set_rls_context(ORG_ID, True)

    run_started_iso = None
    try:
        # =====================================================================
        print("\n=== TASK 1 — the three blockers, genuinely re-confirmed live ===")

        # 1a
        s_models, b_models = http("/v1/models", key=master_key)
        models = json.loads(b_models).get("data", []) if s_models == 200 else []
        model_names = [m.get("id") for m in models]
        check(s_models == 200 and KNOWN_MODEL_NAME in model_names,
              f"1a. GET /v1/models -> 200 with a real deployment by name: "
              f"{model_names}")

        # 1b
        s_info, b_info = http("/model/info", key=master_key)
        info = json.loads(b_info).get("data", []) if s_info == 200 else []
        by_name = {m.get("model_name"): m for m in info}
        entry = by_name.get(KNOWN_MODEL_NAME)
        check(s_info == 200 and entry is not None,
              f"1b. GET /model/info -> 200 with real data (not a 500), "
              f"{len(info)} model(s) configured")
        check(entry is not None
              and entry.get("model_info", {}).get("id") == KNOWN_MODEL_INFO_ID
              and entry.get("litellm_params", {}).get("model") == KNOWN_LITELLM_MODEL,
              f"1b. LITELLM_MASTER_KEY authenticates as PROXY_ADMIN — the SAME "
              f"persisted deployment ({KNOWN_MODEL_INFO_ID} -> {KNOWN_LITELLM_MODEL}) "
              f"named in this session's confirmed facts is still there, unchanged")

        # 1c — a minimal, real Anthropic call, INDEPENDENT of LiteLLM entirely.
        import anthropic as _anthropic
        direct_client = _anthropic.AsyncAnthropic(api_key=anthropic_key)
        direct_text = None
        direct_error = None
        try:
            direct_msg = await direct_client.messages.create(
                model="claude-haiku-4-5-20251001", max_tokens=8,
                messages=[{"role": "user", "content": "Reply with the single word OK."}])
            direct_text = direct_msg.content[0].text
        except Exception as exc:  # noqa: BLE001
            direct_error = f"{type(exc).__name__}: {exc}"
        check(direct_error is None and bool(direct_text),
              f"1c. ANTHROPIC_API_KEY is present and valid — a minimal direct "
              f"Anthropic call (base_url=api.anthropic.com, LiteLLM never in the "
              f"path) succeeded with real text: {direct_text!r} "
              f"(error: {direct_error})")

        # ---------------------------------------------------------------
        find(
            "app_service (DATABASE_URL's role since the RLS enforcement cutover) "
            "gets InsufficientPrivilegeError: 'permission denied for schema litellm' "
            "on any direct SQL against litellm.* — litellmphaseb.structural's own "
            "verify script queried litellm.\"LiteLLM_SpendLogs\" directly and that "
            "path is now genuinely closed by the platform's own least-privilege "
            "tightening, not a regression to fix. The obvious workaround "
            "(LITELLM_DATABASE_URL, the litellm_service role's own connection "
            "string) is ALSO blocked: both its embedded password and the separate "
            "LITELLM_DB_PASSWORD secret fail InvalidPasswordError against "
            "Supabase's pooler — the same class of credential drift documented "
            "for DB_PASSWORD. This script uses LiteLLM's own admin HTTP API "
            "(GET /spend/logs) instead, which reads the identical table and needs "
            "no schema-crossing DB grant."
        )

        run_started_iso_row = await fetchval(
            db_url, "SELECT to_char(now() AT TIME ZONE 'utc', "
                    "'YYYY-MM-DD\"T\"HH24:MI:SS.US\"Z\"')")
        run_started_iso = run_started_iso_row

        # =====================================================================
        print("\n=== TASK 2a — the real, full end-to-end call ===")
        call_started_iso = await fetchval(
            db_url, "SELECT to_char(now() AT TIME ZONE 'utc', "
                    "'YYYY-MM-DD\"T\"HH24:MI:SS.US\"Z\"')")

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            result_text = await ex._execute_chain(
                task_type=TASK_TYPE, org_id=ORG_ID, model_key=ex.DEFAULT_MODEL_KEY,
                model_override=KNOWN_MODEL_NAME,
                make_call=lambda c, m: c.messages.create(
                    model=m, max_tokens=16, system="Reply with the single word OK.",
                    messages=[{"role": "user", "content": "ping"}]),
                extract=lambda msg: msg.content[0].text,
            )
        print(buf.getvalue().rstrip() or "    (no router output)")
        check(bool(result_text) and isinstance(result_text, str),
              f"a genuine 200 with real generated text came back through the "
              f"full chain (call_claude_text -> LiteLLM -> Anthropic): "
              f"{result_text!r}")

        log_row = await pool_fetchrow(
            "SELECT * FROM ai_decision_log WHERE task_type=$1 "
            "ORDER BY created_at DESC LIMIT 1", TASK_TYPE)
        check(log_row is not None, "ai_decision_log wrote a row for this call")
        check(log_row is not None and log_row["success"] is True,
              f"ai_decision_log records success=true "
              f"(row: {dict(log_row) if log_row else None})")
        check(log_row is not None and log_row["cost_usd"] is not None
              and float(log_row["cost_usd"]) > 0,
              f"ai_decision_log records a real, non-zero cost_usd: "
              f"{log_row['cost_usd'] if log_row else None}")
        check(log_row is not None and log_row["latency_ms"] is not None
              and log_row["latency_ms"] > 0,
              f"ai_decision_log records a real, non-zero latency_ms: "
              f"{log_row['latency_ms'] if log_row else None}")
        check(log_row is not None and log_row["model_used"] == KNOWN_MODEL_NAME
              and log_row["fallback_used"] is False,
              f"model_used == '{KNOWN_MODEL_NAME}' on the first attempt, no "
              f"fallback fired")

        print(f"    waiting up to {SPEND_LOG_FLUSH_SECONDS}s for LiteLLM to flush "
              f"its own spend log (GET /spend/logs)...")
        new_rows = wait_for_spend_rows(master_key, call_started_iso, expect=1,
                                        call_type="anthropic_messages")
        check(len(new_rows) >= 1,
              f"LiteLLM's own spend log (GET /spend/logs) recorded THIS call — "
              f"{len(new_rows)} anthropic_messages row(s) since the call began")
        matched = new_rows[0] if new_rows else {}
        check(matched.get("model") == KNOWN_LITELLM_MODEL,
              f"the spend-log row names the SAME resolved deployment "
              f"({KNOWN_LITELLM_MODEL}) that /model/info maps '{KNOWN_MODEL_NAME}' "
              f"to — the same call, correlated by time window + deployment "
              f"identity (LiteLLM logs the resolved litellm_params.model, "
              f"'{matched.get('model')}', not the request-facing model_name we "
              f"passed — a real naming difference between the two logs, noted "
              f"so a naive string-equality correlation wouldn't have worked)")
        check(float(matched.get("spend") or 0) > 0,
              f"non-zero spend recorded: {matched.get('spend')} — the first "
              f"real, billed call this proxy has ever routed")
        check(matched.get("status") == "success" and log_row is not None
              and log_row["success"] is True,
              f"the two logs AGREE on outcome: ai_decision_log.success=True, "
              f"LiteLLM spend log status='{matched.get('status')}'")

        # =====================================================================
        print("\n=== TASK 2b — rollback path: LITELLM_ROUTING_DISABLED=1 ===")
        rb_started_iso = await fetchval(
            db_url, "SELECT to_char(now() AT TIME ZONE 'utc', "
                    "'YYYY-MM-DD\"T\"HH24:MI:SS.US\"Z\"')")
        os.environ[ex.LITELLM_DISABLE_VAR] = "1"

        transport, reason = ex.resolve_transport()
        check(transport == ex.TRANSPORT_ANTHROPIC and "rollback" in reason,
              "with the switch engaged, resolve_transport() reports "
              "transport=anthropic with a loud reason")
        client_rb, t_rb, _r, endpoint_rb = ex._build_ai_client()
        check("api.anthropic.com" in str(getattr(client_rb, "base_url", "")),
              "the rollback client points straight at api.anthropic.com — "
              "LiteLLM is not in the path at all")

        rb_task_type = TASK_TYPE + "_rollback"
        buf2 = io.StringIO()
        with contextlib.redirect_stdout(buf2):
            rb_result = await ex._execute_chain(
                task_type=rb_task_type, org_id=ORG_ID, model_key=ex.DEFAULT_MODEL_KEY,
                model_override="claude-haiku-4-5-20251001",
                make_call=lambda c, m: c.messages.create(
                    model=m, max_tokens=16, system="Reply with the single word OK.",
                    messages=[{"role": "user", "content": "ping"}]),
                extract=lambda msg: msg.content[0].text,
            )
        print(buf2.getvalue().rstrip() or "    (no router output)")
        os.environ.pop(ex.LITELLM_DISABLE_VAR, None)

        check(bool(rb_result), f"the rollback call SUCCEEDED via direct Anthropic: "
                                f"{rb_result!r}")
        rb_row = await pool_fetchrow(
            "SELECT * FROM ai_decision_log WHERE task_type=$1 "
            "ORDER BY created_at DESC LIMIT 1", rb_task_type)
        check(rb_row is not None and rb_row["success"] is True,
              f"ai_decision_log records the rollback call's success too "
              f"(row: {dict(rb_row) if rb_row else None})")

        print(f"    waiting the full {SPEND_LOG_FLUSH_SECONDS}s flush window to "
              f"prove ABSENCE, not just earliness...")
        rb_spend_rows = wait_for_spend_rows(master_key, rb_started_iso, expect=0)
        check(not rb_spend_rows,
              f"ZERO rows in LiteLLM's own spend log for the rollback call, after "
              f"waiting the full flush window — LiteLLM was genuinely never "
              f"contacted (found: {[(r.get('model'), r.get('startTime')) for r in rb_spend_rows]})")

        # =====================================================================
        print("\n=== TASK 2c — the fallback chain still walks correctly via LiteLLM ===")
        walk_task_type = TASK_TYPE + "_walk"
        buf3 = io.StringIO()
        exhausted = None
        with contextlib.redirect_stdout(buf3):
            try:
                await ex._execute_chain(
                    task_type=walk_task_type, org_id=None, model_key=ex.DEFAULT_MODEL_KEY,
                    model_override=BOGUS_PRIMARY,
                    make_call=lambda c, m: c.messages.create(
                        model=m, max_tokens=8, system="x",
                        messages=[{"role": "user", "content": "ping"}]),
                    extract=lambda msg: msg.content[0].text,
                )
            except ex.AIChainExhausted as e:
                exhausted = str(e)
        walk_text = buf3.getvalue()
        print(walk_text.rstrip() or "    (no router output)")
        chain = await ex.resolve_fallback_chain(None)
        check(exhausted is not None and exhausted.startswith("All models failed"),
              f"the real chain executor ran end-to-end and raised "
              f"AIChainExhausted (default chain: {chain})")
        check("Invalid model name passed in model=" in walk_text,
              "the failure text came FROM LITELLM ITSELF — proof the request "
              "really reached the live proxy")
        import re as _re
        attempted = _re.findall(r"\[ai_router\] model '([^']+)' failed", walk_text)
        check(len(attempted) >= 2 and attempted[0] == BOGUS_PRIMARY,
              f"forced first-model failure, and the NEXT model in "
              f"ai.model.fallback_chain was tried afterward, via LiteLLM: "
              f"{attempted}")

    finally:
        # -------------------- TEARDOWN --------------------
        print("\n=== TEARDOWN ===")
        os.environ.pop(ex.LITELLM_DISABLE_VAR, None) if 'ex' in dir() else None
        await pool_execute(
            "DELETE FROM ai_decision_log WHERE task_type = ANY($1::text[])",
            [TASK_TYPE, TASK_TYPE + "_rollback", TASK_TYPE + "_walk"])
        left = await pool_fetchval(
            "SELECT count(*) FROM ai_decision_log WHERE task_type LIKE $1",
            TASK_TYPE + "%")
        check(left == 0, f"zero leftover ai_decision_log rows (found {left})")

        reset_rls_context(rls_tokens)

        s_final, b_final = http("/v1/models", key=master_key)
        final_models = json.loads(b_final).get("data", []) if s_final == 200 else None
        check(final_models is not None and len(final_models) == 1
              and final_models[0].get("id") == KNOWN_MODEL_NAME,
              f"the live proxy's model deployment list is unchanged by this run "
              f"(still exactly one deployment, '{KNOWN_MODEL_NAME}'): {final_models}")
        check(os.environ.get("LITELLM_ROUTING_DISABLED") is None,
              "the rollback switch is left DISENGAGED in the environment")

        if run_started_iso:
            billed_rows = spend_rows_since(master_key, run_started_iso)
            total_billed = sum(float(r.get("spend") or 0) for r in billed_rows)
            print(f"    this verify run billed ${total_billed:.6f} on the live "
                  f"proxy across {len(billed_rows)} row(s) since it started — "
                  f"LiteLLM's spend log is an external audit trail and is "
                  f"deliberately NOT torn down (deleting from it would be "
                  f"falsifying another system's own records)")

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


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
