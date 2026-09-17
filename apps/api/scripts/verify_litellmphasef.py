"""verify_litellmphasef.py — LiteLLM Phase F: force-Anthropic emergency bypass.

Phases A-E and litellmavailability are complete and merged: every production
AI call (text AND embeddings) routes through the self-hosted LiteLLM proxy.
This sprint adds a Hollisworks-only, platform-scoped control that bypasses
LiteLLM entirely for TEXT calls, calling Anthropic directly, so a degraded
proxy is not a platform-wide AI outage. It does NOT build a second bypass
mechanism — LITELLM_ROUTING_DISABLED=1 (proven live, litellmphaseb 25/25)
already IS the direct-Anthropic transport branch
(services.extraction._build_ai_client's TRANSPORT_ANTHROPIC arm); this
sprint adds a second, DB-backed DRIVER of that same branch
(services.platform_ai_controls / services.extraction.resolve_text_transport),
checked only when the env var did NOT already force it.

TASK 1 — DISCOVERY, reported live below, not assumed:

  1a. services.extraction.resolve_transport() (the pre-existing env-var-only
      resolver) is UNCHANGED — still sync, still reads only
      LITELLM_ROUTING_DISABLED / LITELLM_BASE_URL / LITELLM_MASTER_KEY, still
      the resolver services.document_embedding calls DIRECTLY for embeddings.
      The new admin-facing driver lives ENTIRELY in a new, separate,
      text-only path: resolve_text_transport() (async, checks the platform
      DB flag ONLY when resolve_transport() did not already pick Anthropic)
      and _build_text_ai_client() (the client builder _execute_chain now
      calls instead of the unchanged sync _build_ai_client). Inside
      _execute_chain, the platform toggle firing (forced_bypass=True) skips
      resolve_model/resolve_fallback_chain/D2 authorization/disabled-model
      filtering/Phase-E effort entirely and hardcodes
      attempts=[FORCE_ANTHROPIC_BYPASS_MODEL] — the sprint's own "blunt
      instrument, deliberately" requirement. services.document_embedding has
      NO equivalent: it never calls resolve_text_transport, only the
      original resolve_transport, so the platform toggle is structurally
      unreachable from the embedding path.
  1b. org_settings has NO owner_scope column and CANNOT hold a genuine
      platform-scope row (confirmed live below via information_schema) —
      the same real gap litellmphased2's own Task 1a discovery already
      found and worked around with platform_model_catalog. This sprint
      follows the identical, now-established convention: a new table,
      platform_ai_controls, with NO org_id column at all — "no org axis"
      IS the platform scope (CLAUDE.md Rule 6). Confirmed live below.
  1c. The existing rollback path (services.extraction._build_ai_client's
      TRANSPORT_ANTHROPIC arm) applies ZERO deployment-name translation —
      whatever resolve_model() resolved (an org_settings value like
      'claude-haiku', a PROXY DEPLOYMENT name, never a real Anthropic model
      id) is sent to api.anthropic.com literally. verify_litellmphasebproof.py
      already had to hand a real, dated id ('claude-haiku-4-5-20251001') via
      model_override to make its own direct-Anthropic proof call succeed —
      this sprint's fixed constant, FORCE_ANTHROPIC_BYPASS_MODEL, is that
      SAME real, dated id (the platform's own default-safe model's real
      upstream identity, per the seed row in
      migrations/litellmphased2_model_catalog.sql), confirmed LIVE below to
      be genuinely different from every one of LiteLLM's own registered
      deployment names (GET /model/info).

EMBEDDING DECISION (stated plainly, per the sprint's own requirement):
embeddings KEEP ROUTING THROUGH LITELLM, completely unaffected by this
toggle, proven below by a real Voyage call made WHILE the bypass is
engaged. This is not a workaround — Voyage is not Anthropic, there is no
direct-Anthropic equivalent for an embedding call, and the toggle exists for
an Anthropic-specific incident; degrading a healthy embedding path along
with it would be an unrelated, unrequested blast-radius increase. See
services.document_embedding's own module docstring for the full reasoning.

Hydrates DATABASE_URL (and LITELLM_BASE_URL / LITELLM_MASTER_KEY /
ANTHROPIC_API_KEY) from Doppler over HTTPS at startup — the
verify_litellmavailability.py pattern. run_sprint.sh's Step 3 does NOT
`doppler run --` this script. Never prints a credential value.

Cost note: 3 real Anthropic calls (max_tokens<=16) + 1 real Voyage embedding
call (a short fixture sentence).

Run:  python3 apps/api/scripts/verify_litellmphasef.py
"""
from __future__ import annotations

import asyncio
import json
import os
import pathlib
import sys
import time
import urllib.error
import urllib.request
from uuid import UUID

HERE = pathlib.Path(__file__).resolve()
sys.path.insert(0, str(HERE.parent))
from _db_bootstrap import bootstrap_async  # noqa: E402

HEADERS = {"Authorization": "Bearer verify-token"}

ORG_ID = UUID("00000000-0000-0000-0000-000000000001")   # 2nd Act Capital (real)

FIXTURE_ORG_ID = UUID("99000000-0000-0000-0000-0000000f0a01")
FIXTURE_SUPERADMIN_ID = UUID("99000000-0000-0000-0000-0000000f0a02")
FIXTURE_ORGADMIN_ID = UUID("99000000-0000-0000-0000-0000000f0a03")
FIXTURE_SUPERADMIN_SUB = "auth0|verify_phasef_superadmin"
FIXTURE_ORGADMIN_SUB = "auth0|verify_phasef_orgadmin"

TASK_OFF = "verify_litellmphasef_off"
TASK_ON = "verify_litellmphasef_on"
TASK_RESTORED = "verify_litellmphasef_restored"
SPEND_LOG_FLUSH_SECONDS = 45
OVERRIDE_MODEL_IGNORED = "claude-sonnet"  # a real, DIFFERENT model — proves the override is ignored

_ok = True
_n_pass = 0
_n_fail = 0
_finds: list[str] = []


def check(label, passed, detail=""):
    global _ok, _n_pass, _n_fail
    print(f"{'[PASS]' if passed else '[FAIL]'} {label}" + (f"  — {detail}" if detail else ""))
    if passed:
        _n_pass += 1
    else:
        _n_fail += 1
        _ok = False
    return passed


def find(label, detail=""):
    print(f"[FIND] {label}" + (f"  — {detail}" if detail else ""))
    _finds.append(label)


class _Principal:
    """Drives the real ASGI app as one user — verify_litellmphased2.py's
    pattern verbatim."""

    def __init__(self, client, sub, org_id):
        self.client, self.sub, self.org_id = client, sub, org_id

    def call(self, method, path, body=None):
        import main
        sub, org_id = self.sub, str(self.org_id)
        main.verify_token = lambda _t: {
            "sub": sub, "email": f"{sub}@test.local", "org_id": org_id,
        }
        fn = getattr(self.client, method)
        return fn(path, headers=HEADERS, **({"json": body} if body is not None else {}))


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


def _litellm_http(path, *, method="GET", body=None, timeout=60, retries=3):
    base = os.environ.get("LITELLM_BASE_URL", "").rstrip("/")
    key = os.environ.get("LITELLM_MASTER_KEY", "")
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


def spend_logs(key):
    s, b = _litellm_http("/spend/logs")
    if s != 200:
        return None
    try:
        return json.loads(b)
    except Exception:  # noqa: BLE001
        return None


def spend_rows_since(key, since_iso: str, *, call_type=None):
    rows = spend_logs(key) or []
    out = [r for r in rows if (r.get("startTime") or "") >= since_iso]
    if call_type:
        out = [r for r in out if r.get("call_type") == call_type]
    return out


def wait_for_spend_rows(key, since_iso: str, *, expect: int, call_type=None,
                         timeout=SPEND_LOG_FLUSH_SECONDS):
    deadline = time.monotonic() + timeout
    rows = []
    while time.monotonic() < deadline:
        rows = spend_rows_since(key, since_iso, call_type=call_type)
        if expect and len(rows) >= expect:
            return rows
        time.sleep(2)
    return rows


async def setup_fixtures(pool):
    from services.database import reset_rls_context, set_rls_context
    from services.rbac import grant_org_admin

    tokens = set_rls_context(None, True)
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO organizations (id, name, slug)
                VALUES ($1, $2, $3)
                ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name
                """,
                FIXTURE_ORG_ID, "Verify Phase F Org", "verify-phasef-org",
            )
            for user_id, sub, role, label in (
                (FIXTURE_SUPERADMIN_ID, FIXTURE_SUPERADMIN_SUB, "super_admin", "Super Admin"),
                (FIXTURE_ORGADMIN_ID, FIXTURE_ORGADMIN_SUB, "member", "Org Admin"),
            ):
                await conn.execute(
                    """
                    INSERT INTO users (id, org_id, email, full_name, auth0_sub, role)
                    VALUES ($1, $2, $3, $4, $5, $6)
                    ON CONFLICT (id) DO UPDATE SET auth0_sub = EXCLUDED.auth0_sub,
                        org_id = EXCLUDED.org_id, role = EXCLUDED.role
                    """,
                    user_id, FIXTURE_ORG_ID, f"{sub}@test.local",
                    f"Verify Phase F {label}", sub, role,
                )
            await grant_org_admin(conn, FIXTURE_ORGADMIN_ID, FIXTURE_ORG_ID)
    finally:
        reset_rls_context(tokens)


async def teardown_fixtures(pool):
    from services.database import reset_rls_context, set_rls_context

    tokens = set_rls_context(None, True)
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM user_roles WHERE role_id IN "
                "(SELECT id FROM roles WHERE org_id = $1)", FIXTURE_ORG_ID,
            )
            await conn.execute(
                "DELETE FROM role_permissions WHERE role_id IN "
                "(SELECT id FROM roles WHERE org_id = $1)", FIXTURE_ORG_ID,
            )
            await conn.execute("DELETE FROM roles WHERE org_id = $1", FIXTURE_ORG_ID)
            await conn.execute("DELETE FROM users WHERE org_id = $1", FIXTURE_ORG_ID)
            await conn.execute("DELETE FROM organizations WHERE id = $1", FIXTURE_ORG_ID)
    finally:
        reset_rls_context(tokens)


async def main() -> int:
    db_url = await bootstrap_async(quiet=True)
    if not db_url:
        print("FATAL: no working DATABASE_URL from Doppler.")
        return 2

    for var in ("LITELLM_BASE_URL", "LITELLM_MASTER_KEY", "ANTHROPIC_API_KEY"):
        if not os.environ.get(var):
            print(f"FATAL: {var} not present after Doppler hydration.")
            return 2

    for sp in sorted((HERE.parents[1]).glob("venv/lib/python3*/site-packages")):
        if str(sp) not in sys.path:
            sys.path.insert(0, str(sp))
    api_dir = HERE.parents[1]
    if str(api_dir) not in sys.path:
        sys.path.insert(0, str(api_dir))

    import services.document_embedding as de
    import services.extraction as ex
    import services.platform_ai_controls as pac
    from services.database import close_pool, get_pool, reset_rls_context, set_rls_context

    master_key = os.environ.get("LITELLM_MASTER_KEY", "")

    baseline_deployments = {m.get("model_name") for m in
                             json.loads(_litellm_http("/model/info")[1]).get("data", [])}
    print(f"baseline LiteLLM deployments: {sorted(baseline_deployments)}")

    # A single super-admin RLS context for the ENTIRE run, set ONCE here and
    # reset ONCE in the outer `finally` — NOT per-call. ai_decision_log and
    # platform_ai_controls are both RLS-protected (CLAUDE.md "RLS Is Now
    # Genuinely Enforced"); every direct DB touch below needs this, mirroring
    # verify_litellmphasec.py's own `rls_tokens = set_rls_context(...)`
    # pattern. NEVER cache a `pool` object across a `pool_fetch*`/`pool_execute`
    # call (each of those closes+reopens the pool to stay bound to whichever
    # event loop is calling — TestClient's own portal thread has a SEPARATE
    # loop from this coroutine's) or across the TestClient block below —
    # always re-fetch `await get_pool()` immediately before use instead.
    rls_tokens = set_rls_context(str(ORG_ID), True)
    embed_log_id = None
    try:
        # ═════════════════════════════════════════════════════════════════
        print("\n=== TASK 1: DISCOVER (three findings, reported explicitly) ===")

        import inspect

        embed_src = inspect.getsource(de._execute_embedding_chain) + inspect.getsource(
            de._embedding_credential_state)
        check(
            "ex.resolve_transport()" in embed_src
            and "resolve_text_transport" not in embed_src,
            "1a. services.document_embedding calls the ORIGINAL resolve_transport() "
            "only — resolve_text_transport/_build_text_ai_client (the two new "
            "Phase F entry points) appear NOWHERE in the embedding module, so "
            "the platform toggle is structurally unreachable from embeddings",
        )
        check(
            hasattr(ex, "resolve_text_transport") and hasattr(ex, "_build_text_ai_client")
            and hasattr(ex, "_build_ai_client") and hasattr(ex, "FORCE_ANTHROPIC_BYPASS_MODEL"),
            "1a. the new text-only entry points exist alongside the UNCHANGED "
            f"_build_ai_client; fixed bypass model = {ex.FORCE_ANTHROPIC_BYPASS_MODEL!r}",
        )
        chain_src = inspect.getsource(ex._execute_chain)
        check(
            "if forced_bypass:" in chain_src and "FORCE_ANTHROPIC_BYPASS_MODEL" in chain_src
            and "resolve_authorized_models" in chain_src,
            "1a. _execute_chain branches on forced_bypass BEFORE the D2 "
            "authorization/disabled-model/effort logic — that whole block "
            "lives in the else arm, skipped entirely when the platform "
            "toggle fires",
        )

        org_settings_cols = {r["column_name"] for r in await pool_fetch(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'org_settings'")}
        check("owner_scope" not in org_settings_cols and "org_id" in org_settings_cols,
              f"1b. org_settings genuinely has NO owner_scope column (columns: "
              f"{sorted(org_settings_cols)}) — confirms no platform-scope row "
              f"is possible there")
        pac_cols = {r["column_name"] for r in await pool_fetch(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'platform_ai_controls'")}
        check("org_id" not in pac_cols and {"key", "enabled"} <= pac_cols,
              f"1b. the new platform_ai_controls table has NO org_id column at "
              f"all — same convention as platform_model_catalog (columns: "
              f"{sorted(pac_cols)}); this table IS the platform scope")
        pac_policies = {r["cmd"] for r in await pool_fetch(
            "SELECT cmd FROM pg_policies WHERE tablename = 'platform_ai_controls'")}
        check(pac_policies == {"SELECT", "INSERT", "UPDATE", "DELETE"},
              f"1b. platform_ai_controls has a real RLS policy for EVERY "
              f"operation (the per-operation lesson from platform_model_"
              f"catalog's missing-UPDATE trap): {sorted(pac_policies)}")

        check(ex.FORCE_ANTHROPIC_BYPASS_MODEL not in baseline_deployments,
              f"1c. the fixed bypass model "
              f"({ex.FORCE_ANTHROPIC_BYPASS_MODEL!r}) is genuinely NOT one of "
              f"LiteLLM's own registered deployment names "
              f"({sorted(baseline_deployments)}) — it is a real, dated, "
              f"upstream Anthropic id, never a proxy deployment name")
        find(
            "1c. the pre-existing rollback path applies ZERO deployment-name "
            "translation on the TRANSPORT_ANTHROPIC branch: a caller that hit "
            "the OLD env-var rollback via the ordinary code path (resolve_model "
            "returning an org_settings value like 'claude-haiku') would send "
            "that literal proxy deployment name straight to api.anthropic.com "
            "and get a real 404 — every existing rollback proof "
            "(verify_litellmphasebproof.py) only succeeded because it passed "
            "model_override to a real dated id by hand. Phase F's fixed "
            "constant is what makes the NEW admin-facing toggle safe to flip "
            "without requiring an operator to also remember a real model id."
        )

        # ═════════════════════════════════════════════════════════════════
        print("\n=== TASK 2/4 — baseline state ===")
        pool = await get_pool()
        async with pool.acquire() as conn:
            baseline_row = await pac.get_force_anthropic_bypass(conn)
        check(baseline_row is not None,
              f"2. the seeded force_anthropic_bypass row exists: {dict(baseline_row)}")
        if baseline_row["enabled"]:
            find("the platform bypass was found ALREADY ENABLED at the start "
                 "of this run — not this script's doing; forcing it OFF "
                 "before proceeding so the run's own OFF-baseline proof is "
                 "meaningful.")
            pool = await get_pool()
            async with pool.acquire() as conn:
                await pac.set_force_anthropic_bypass(conn, enabled=False, updated_by=None)

        pool = await get_pool()
        await setup_fixtures(pool)

        # ═════════════════════════════════════════════════════════════════
        # TestClient block — SELF-CONTAINED: every before/after proof here is
        # via ANOTHER HTTP call, never a direct DB read, because TestClient
        # runs the ASGI app on its OWN portal thread/event loop (a SEPARATE
        # loop from this coroutine's). `close_pool()` before entering and
        # right after exiting is the verify_litellmphased2.py /
        # verify_litellmavailability.py pattern: it forces whichever loop
        # touches the pool next to (re)create it fresh, bound to that loop —
        # skipping this bracketing is exactly what produced "Future attached
        # to a different loop" / "another operation is in progress" the
        # first time this script ran.
        print("\n=== TASK 2 — admin endpoint access control (self-contained, HTTP only) ===")
        await close_pool()
        from starlette.testclient import TestClient
        import main as main_module

        client = TestClient(main_module.app, raise_server_exceptions=False)
        client.__enter__()
        try:
            superadmin = _Principal(client, FIXTURE_SUPERADMIN_SUB, FIXTURE_ORG_ID)
            orgadmin = _Principal(client, FIXTURE_ORGADMIN_SUB, FIXTURE_ORG_ID)

            r_get_super = superadmin.call("get", "/api/v1/admin/ai/force-anthropic-bypass")
            check(r_get_super.status_code == 200 and r_get_super.json()["enabled"] is False,
                  f"super_admin GET reflects the OFF default: HTTP "
                  f"{r_get_super.status_code} {r_get_super.text[:200]}")
            r_get_org = orgadmin.call("get", "/api/v1/admin/ai/force-anthropic-bypass")
            check(r_get_org.status_code == 403,
                  f"org_admin GET is refused too (this is a Hollisworks-only "
                  f"control, not merely write-gated): HTTP {r_get_org.status_code}")

            r_extra = superadmin.call(
                "put", "/api/v1/admin/ai/force-anthropic-bypass",
                body={"enabled": True, "org_id": str(ORG_ID)},
            )
            check(r_extra.status_code == 422,
                  f"a request body naming org_id is refused outright by "
                  f"Pydantic extra='forbid' (CLAUDE.md Rule 6) — never "
                  f"silently accepted and ignored: HTTP {r_extra.status_code}")

            r_org_toggle = orgadmin.call(
                "put", "/api/v1/admin/ai/force-anthropic-bypass", body={"enabled": True})
            check(r_org_toggle.status_code == 403,
                  f"org_admin PUT is refused: HTTP {r_org_toggle.status_code}")
            r_still_off = superadmin.call("get", "/api/v1/admin/ai/force-anthropic-bypass")
            check(r_still_off.status_code == 200 and r_still_off.json()["enabled"] is False,
                  "the refused org_admin write left the platform setting "
                  "genuinely UNCHANGED (proven by an INDEPENDENT super_admin "
                  "GET, not just the 403 status code)")

            r_on = superadmin.call(
                "put", "/api/v1/admin/ai/force-anthropic-bypass", body={"enabled": True})
            check(r_on.status_code == 200 and r_on.json()["enabled"] is True,
                  f"super_admin PUT enables the bypass: HTTP {r_on.status_code} "
                  f"{r_on.text[:200]}")
            r_now_on = superadmin.call("get", "/api/v1/admin/ai/force-anthropic-bypass")
            check(r_now_on.status_code == 200 and r_now_on.json()["enabled"] is True,
                  "the write genuinely PERSISTED — an INDEPENDENT GET call "
                  "confirms it, not just the PUT response body")

            r_off_again = superadmin.call(
                "put", "/api/v1/admin/ai/force-anthropic-bypass", body={"enabled": False})
            check(r_off_again.status_code == 200 and r_off_again.json()["enabled"] is False,
                  f"super_admin PUT can disable it again too: HTTP "
                  f"{r_off_again.status_code}")
        finally:
            client.__exit__(None, None, None)
        await close_pool()
        # ═════════════════ end TestClient block — flag is OFF ═════════════

        # ─────────────────────────────────────────────────────────────
        print("\n=== TASK 4 — Bypass OFF: unchanged, lands in LiteLLM's spend log ===")
        off_started_iso = await pool_fetchval(
            "SELECT to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD\"T\"HH24:MI:SS.US\"Z\"')")
        off_result = await ex.call_claude_text(
            "Reply with the single word OK.", [{"role": "user", "content": "ping"}],
            max_tokens=16, org_id=ORG_ID, task_type=TASK_OFF,
        )
        check(bool(off_result), f"OFF: the call succeeded via LiteLLM: {off_result!r}")
        off_log = await pool_fetchrow(
            "SELECT * FROM ai_decision_log WHERE task_type = $1 "
            "ORDER BY created_at DESC LIMIT 1", TASK_OFF)
        check(off_log is not None and off_log["litellm_bypassed"] is False
              and off_log["bypass_reason"] is None,
              f"OFF: ai_decision_log shows litellm_bypassed=False, "
              f"bypass_reason=None: {dict(off_log) if off_log else None}")
        off_spend = wait_for_spend_rows(master_key, off_started_iso, expect=1,
                                         call_type="anthropic_messages")
        check(len(off_spend) >= 1,
              f"OFF: LiteLLM's own spend log recorded this call — "
              f"{len(off_spend)} anthropic_messages row(s) since it began")

        # ─────────────────────────────────────────────────────────────
        # Drive the bypass ON directly (super-admin RLS context already
        # active on this coroutine) — the HTTP toggle mechanism itself was
        # already proven above; this is the SAME underlying service function
        # the router calls, just without re-entering TestClient.
        pool = await get_pool()
        async with pool.acquire() as conn:
            await pac.set_force_anthropic_bypass(conn, enabled=True, updated_by=None)

        print("\n=== TASK 4 — Bypass ON: direct Anthropic, NO new LiteLLM spend row ===")
        on_started_iso = await pool_fetchval(
            "SELECT to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD\"T\"HH24:MI:SS.US\"Z\"')")
        on_result = await ex.call_claude_text(
            "Reply with the single word OK.", [{"role": "user", "content": "ping"}],
            max_tokens=16, model=OVERRIDE_MODEL_IGNORED, org_id=ORG_ID, task_type=TASK_ON,
        )
        check(bool(on_result), f"ON: the call SUCCEEDED via direct Anthropic: {on_result!r}")
        on_log = await pool_fetchrow(
            "SELECT * FROM ai_decision_log WHERE task_type = $1 "
            "ORDER BY created_at DESC LIMIT 1", TASK_ON)
        check(
            on_log is not None
            and on_log["model_requested"] == ex.FORCE_ANTHROPIC_BYPASS_MODEL
            and on_log["model_used"] == ex.FORCE_ANTHROPIC_BYPASS_MODEL,
            f"ON: the caller's requested override "
            f"({OVERRIDE_MODEL_IGNORED!r}) was IGNORED — model_requested and "
            f"model_used both resolved to the ONE fixed bypass model "
            f"({ex.FORCE_ANTHROPIC_BYPASS_MODEL!r}), proving this is a "
            f"blunt, non-per-task override: {dict(on_log) if on_log else None}",
        )
        check(on_log is not None and on_log["litellm_bypassed"] is True
              and on_log["bypass_reason"] and "force_anthropic_bypass" in on_log["bypass_reason"],
              f"ON: ai_decision_log records the call CLEARLY MARKED as "
              f"bypassed: litellm_bypassed={on_log['litellm_bypassed'] if on_log else None}, "
              f"bypass_reason={on_log['bypass_reason'][:120] if on_log and on_log['bypass_reason'] else None}...")

        print(f"    waiting the full {SPEND_LOG_FLUSH_SECONDS}s flush window to "
              f"prove ABSENCE, not just earliness...")
        on_spend = wait_for_spend_rows(master_key, on_started_iso, expect=0)
        check(not on_spend,
              f"ON: ZERO rows in LiteLLM's own spend log for the bypass call, "
              f"after waiting the full flush window — LiteLLM was genuinely "
              f"never contacted: "
              f"{[(r.get('model'), r.get('startTime')) for r in on_spend]}")

        # ─────────────────────────────────────────────────────────────
        print("\n=== TASK 4 — the embedding path is UNAFFECTED while ON ===")
        embed_started_at = await pool_fetchval("SELECT now()")
        pool = await get_pool()
        embed_vec = await de.embed_query(
            pool, ORG_ID, "verify_litellmphasef embedding probe — must stay on LiteLLM")
        check(len(embed_vec) == de.EMBEDDING_DIMENSIONS,
              f"embedding call SUCCEEDED at the expected dimensionality "
              f"({len(embed_vec)}) while the text bypass is engaged")
        embed_log = await pool_fetchrow(
            "SELECT * FROM ai_decision_log WHERE task_type = $1 AND org_id = $2 "
            "AND created_at >= $3 "
            "ORDER BY created_at DESC LIMIT 1",
            de.EMBED_TASK_QUERY, ORG_ID, embed_started_at,
        )
        embed_log_id = embed_log["id"] if embed_log else None
        check(embed_log is not None and embed_log["litellm_bypassed"] is False,
              f"the embedding call's OWN ai_decision_log row shows "
              f"litellm_bypassed=False — it stayed on LiteLLM the entire "
              f"time the text bypass was engaged: "
              f"{dict(embed_log) if embed_log else None}")

        # ─────────────────────────────────────────────────────────────
        print("\n=== TASK 4 — RLS itself blocks a non-super-admin write (defense in depth) ===")
        nonadmin_tokens = set_rls_context(str(FIXTURE_ORG_ID), False)
        try:
            pool = await get_pool()
            async with pool.acquire() as conn:
                nonadmin_result = await conn.execute(
                    "UPDATE platform_ai_controls SET enabled = false WHERE key = $1",
                    pac.FORCE_ANTHROPIC_BYPASS_KEY,
                )
        finally:
            reset_rls_context(nonadmin_tokens)  # restores the outer super-admin context
        check(nonadmin_result == "UPDATE 0",
              f"a non-super-admin RLS context updates ZERO rows at the "
              f"DATABASE layer (not merely refused by the router's own "
              f"app-side check): {nonadmin_result!r}")
        pool = await get_pool()
        async with pool.acquire() as conn:
            still_on = await pac.get_force_anthropic_bypass(conn)
        check(still_on["enabled"] is True,
              "...and the value is confirmed genuinely unchanged by that "
              "blocked attempt")

        # ─────────────────────────────────────────────────────────────
        print("\n=== TASK 4 — toggling back OFF restores LiteLLM routing ===")
        pool = await get_pool()
        async with pool.acquire() as conn:
            await pac.set_force_anthropic_bypass(conn, enabled=False, updated_by=None)

        restored_started_iso = await pool_fetchval(
            "SELECT to_char(now() AT TIME ZONE 'utc', 'YYYY-MM-DD\"T\"HH24:MI:SS.US\"Z\"')")
        restored_result = await ex.call_claude_text(
            "Reply with the single word OK.", [{"role": "user", "content": "ping"}],
            max_tokens=16, org_id=ORG_ID, task_type=TASK_RESTORED,
        )
        check(bool(restored_result), f"RESTORED: the call succeeded again: {restored_result!r}")
        restored_log = await pool_fetchrow(
            "SELECT * FROM ai_decision_log WHERE task_type = $1 "
            "ORDER BY created_at DESC LIMIT 1", TASK_RESTORED)
        check(restored_log is not None and restored_log["litellm_bypassed"] is False,
              f"RESTORED: litellm_bypassed=False again — the toggle genuinely "
              f"reverses: {dict(restored_log) if restored_log else None}")
        restored_spend = wait_for_spend_rows(master_key, restored_started_iso, expect=1,
                                              call_type="anthropic_messages")
        check(len(restored_spend) >= 1,
              f"RESTORED: LiteLLM's own spend log recorded this call again — "
              f"{len(restored_spend)} row(s)")

    finally:
        # -------------------- TEARDOWN (always runs, even on a raise) ------
        print("\n=== TEARDOWN ===")
        try:
            await close_pool()
            pool = await get_pool()
            async with pool.acquire() as conn:
                await pac.set_force_anthropic_bypass(conn, enabled=False, updated_by=None)
            final_state = await pool_fetchrow(
                "SELECT enabled FROM platform_ai_controls WHERE key = 'force_anthropic_bypass'")
            check(final_state is not None and final_state["enabled"] is False,
                  "the platform bypass is left OFF")

            await pool_execute(
                "DELETE FROM ai_decision_log WHERE task_type = ANY($1::text[])",
                [TASK_OFF, TASK_ON, TASK_RESTORED],
            )
            if embed_log_id is not None:
                await pool_execute("DELETE FROM ai_decision_log WHERE id = $1", embed_log_id)
            left_logs = await pool_fetchval(
                "SELECT count(*) FROM ai_decision_log WHERE task_type = ANY($1::text[]) "
                "OR id = $2",
                [TASK_OFF, TASK_ON, TASK_RESTORED], embed_log_id,
            )
            check(left_logs == 0, f"zero leftover ai_decision_log rows (found {left_logs})")

            pool = await get_pool()
            await teardown_fixtures(pool)
            left_org = await pool_fetchval(
                "SELECT count(*) FROM organizations WHERE id = $1", FIXTURE_ORG_ID)
            check(left_org == 0, "zero leftover fixture organization rows")

            s_final, b_final = _litellm_http("/model/info")
            final_deployments = ({m.get("model_name") for m in json.loads(b_final).get("data", [])}
                                  if s_final == 200 else None)
            check(final_deployments == baseline_deployments,
                  f"the live proxy's deployment set is byte-for-byte unchanged: "
                  f"{sorted(final_deployments) if final_deployments else final_deployments}")
        finally:
            reset_rls_context(rls_tokens)

    print(f"\n{'=' * 70}\nTOTAL: {_n_pass} PASS, {_n_fail} FAIL, {len(_finds)} FIND\n{'=' * 70}")
    return 0 if _ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
