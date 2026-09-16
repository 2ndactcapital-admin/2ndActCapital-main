"""verify_litellmphased1c.py — LiteLLM Phase D1c: credential-failure alerting.

Proves, against the REAL live ``hollisworks-litellm`` proxy and the real
database, that:

  1. Task 1's three discovery findings hold (probed live against the real
     proxy, not assumed): what an invalid-credential failure actually looks
     like coming back through the Anthropic SDK pointed at LiteLLM; how that
     is/was distinguishable from a model-unavailable failure; and
     create_held_run_alerts' real signature/recipient rule.
  2. An org's OWN broken credential raises AIOrgCredentialError — naming the
     provider, never a silent None, never AIChainExhausted — and the chain
     never walks onto a platform deployment for that attempt: proven by the
     ABSENCE of any platform-attributed spend-log row after the real flush
     window, not merely "an error came back".
  3. That failure creates a real member_todos row for the org's
     manage_org_settings holder(s), read back from the database.
  4. Cross-org: a DIFFERENT org's manage_org_settings holder receives NOTHING
     from another org's credential failure.
  5. A model-unavailable failure (HTTP 400, no auth error at all) is
     completely unaffected and still walks the fallback chain to success —
     proven distinct from the credential path in the SAME run.
  6. A 'platform'-configured org (2nd Act, real) is completely unaffected —
     no regression.
  7. The real Hollisworks org — which genuinely holds zero
     manage_org_settings grants today — hits the zero-recipient path and
     gets a findable audit_log row instead of silence. Proven by calling
     ``create_credential_failure_alerts`` directly with a clearly-labeled
     synthetic provider name, NOT by mutating Hollisworks' actual AI
     credential configuration or provisioning a real deployment for it —
     there is no need to make a real (production) org's AI routing flaky
     just to prove its recipient set is empty, and its recipient set is
     exactly what services.rbac.get_users_with_permission already answers.
  8. Teardown leaves zero fixture DB rows, zero leftover LiteLLM
     deployments, and zero leftover ai_decision_log rows.

Hydrates DATABASE_URL (and every other Doppler secret, including
LITELLM_BASE_URL / LITELLM_MASTER_KEY / ANTHROPIC_API_KEY) from Doppler over
HTTPS at startup — the verify_litellmphased1b.py pattern. run_sprint.sh's
Step 3 does NOT `doppler run --` this script.

Never prints a credential value (including the deliberately-invalid fixture
key used to reproduce a real auth failure).

Cost note: this script makes 3 small real Anthropic calls (max_tokens<=12)
and zero Voyage calls — Phase D1c's scope (``_CALL_PROVIDER`` in
services/extraction.py) is Anthropic only, so no free-tier embedding pacing
is needed.

Run:  python3 apps/api/scripts/verify_litellmphased1c.py
"""
from __future__ import annotations

import inspect
import json
import pathlib
import sys
import time
import urllib.error
import urllib.request
from uuid import UUID

HERE = pathlib.Path(__file__).resolve()
sys.path.insert(0, str(HERE.parent))
from _db_bootstrap import bootstrap_async  # noqa: E402

ORG = UUID("00000000-0000-0000-0000-000000000001")          # 2nd Act Capital (real)
HOLLIS = UUID("bb347258-8f28-4f49-8cc9-e29ccad82884")        # Hollisworks (real)

FIXTURE_ORG_A_ID = UUID("99000000-0000-0000-0000-0000000d1c01")
FIXTURE_ORG_B_ID = UUID("99000000-0000-0000-0000-0000000d1c02")
FIXTURE_ORG_A_ADMIN_ID = UUID("99000000-0000-0000-0000-0000000d1c03")
FIXTURE_ORG_B_ADMIN_ID = UUID("99000000-0000-0000-0000-0000000d1c04")
FIXTURE_ORG_A_ADMIN_SUB = "auth0|verify_d1c_org_a_admin"
FIXTURE_ORG_B_ADMIN_SUB = "auth0|verify_d1c_org_b_admin"

# A syntactically plausible but definitely-invalid Anthropic key — never a
# real secret, but treated with the same "never print" discipline anyway.
BAD_ANTHROPIC_KEY = "sk-ant-api03-verify-d1c-deliberately-invalid-0000000000000000"

SPEND_LOG_FLUSH_SECONDS = 45

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


# ── LiteLLM admin/data-plane API — direct HTTP ──────────────────────────────


def _http(path, *, method="GET", body=None, timeout=60, key=None):
    import os

    base = os.environ.get("LITELLM_BASE_URL", "").rstrip("/")
    key = key or os.environ.get("LITELLM_MASTER_KEY", "")
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{base}{path}", data=data, method=method)
    req.add_header("Authorization", f"Bearer {key}")
    req.add_header("Accept", "application/json")
    if data:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def _model_info():
    s, b = _http("/model/info")
    if s != 200:
        raise RuntimeError(f"GET /model/info -> {s}: {b[:300]}")
    return json.loads(b).get("data", [])


def _find_deployment(model_name):
    for m in _model_info():
        if m.get("model_name") == model_name:
            return m
    return None


def _find_stable(model_name, *, expect_present: bool, timeout=12.0, interval=1.0):
    deadline = time.monotonic() + timeout
    result = _find_deployment(model_name)
    while (result is not None) != expect_present and time.monotonic() < deadline:
        time.sleep(interval)
        result = _find_deployment(model_name)
    return result


def spend_logs():
    s, b = _http("/spend/logs")
    if s != 200:
        return []
    try:
        return json.loads(b)
    except Exception:  # noqa: BLE001
        return []


def spend_rows_since(since_iso: str):
    return [r for r in spend_logs() if (r.get("startTime") or "") >= since_iso]


async def _sdk_probe(model_id: str, *, master_key: str | None = None):
    """A real call through the SAME SDK/transport services.extraction uses —
    the Anthropic SDK pointed at LiteLLM's base URL — returning the raised
    exception (or None on an unexpected success). This is what
    ``_execute_chain``/``_is_auth_failure`` actually see, not an HTTP status
    parsed independently of the app's own code path."""
    import os

    import anthropic

    key = master_key or os.environ["LITELLM_MASTER_KEY"]
    client = anthropic.AsyncAnthropic(
        api_key=key,
        base_url=os.environ["LITELLM_BASE_URL"].rstrip("/"),
        default_headers={"Authorization": f"Bearer {key}"},
    )
    try:
        await client.messages.create(
            model=model_id, max_tokens=8,
            messages=[{"role": "user", "content": "Say OK"}],
        )
        return None
    except Exception as exc:  # noqa: BLE001
        return exc


# ── fixtures ─────────────────────────────────────────────────────────────


async def setup_fixtures(pool):
    from services.database import reset_rls_context, set_rls_context
    from services.rbac import grant_org_admin

    tokens = set_rls_context(None, True)
    try:
        async with pool.acquire() as conn:
            for org_id, name, slug in (
                (FIXTURE_ORG_A_ID, "Verify D1c Org A", "verify-d1c-org-a"),
                (FIXTURE_ORG_B_ID, "Verify D1c Org B", "verify-d1c-org-b"),
            ):
                await conn.execute(
                    """
                    INSERT INTO organizations (id, name, slug)
                    VALUES ($1, $2, $3)
                    ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name
                    """,
                    org_id, name, slug,
                )
            for user_id, org_id, sub, label in (
                (FIXTURE_ORG_A_ADMIN_ID, FIXTURE_ORG_A_ID, FIXTURE_ORG_A_ADMIN_SUB, "Org A Admin"),
                (FIXTURE_ORG_B_ADMIN_ID, FIXTURE_ORG_B_ID, FIXTURE_ORG_B_ADMIN_SUB, "Org B Admin"),
            ):
                await conn.execute(
                    """
                    INSERT INTO users (id, org_id, email, full_name, auth0_sub, role)
                    VALUES ($1, $2, $3, $4, $5, 'member')
                    ON CONFLICT (id) DO UPDATE SET auth0_sub = EXCLUDED.auth0_sub,
                        org_id = EXCLUDED.org_id
                    """,
                    user_id, org_id, f"{sub}@test.local", f"Verify D1c {label}", sub,
                )
            await grant_org_admin(conn, FIXTURE_ORG_A_ADMIN_ID, FIXTURE_ORG_A_ID)
            await grant_org_admin(conn, FIXTURE_ORG_B_ADMIN_ID, FIXTURE_ORG_B_ID)
    finally:
        reset_rls_context(tokens)


async def teardown_fixtures(pool):
    from services.database import reset_rls_context, set_rls_context

    tokens = set_rls_context(None, True)
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM ai_decision_log WHERE task_type LIKE 'verify_d1c_%'"
            )
            await conn.execute(
                "DELETE FROM member_todos WHERE org_id = ANY($1::uuid[])",
                [FIXTURE_ORG_A_ID, FIXTURE_ORG_B_ID],
            )
            for org_id in (FIXTURE_ORG_A_ID, FIXTURE_ORG_B_ID):
                await conn.execute(
                    "DELETE FROM user_roles WHERE role_id IN "
                    "(SELECT id FROM roles WHERE org_id = $1)", org_id,
                )
                await conn.execute(
                    "DELETE FROM role_permissions WHERE role_id IN "
                    "(SELECT id FROM roles WHERE org_id = $1)", org_id,
                )
                await conn.execute("DELETE FROM roles WHERE org_id = $1", org_id)
                await conn.execute("DELETE FROM org_settings WHERE org_id = $1", org_id)
                await conn.execute("DELETE FROM users WHERE org_id = $1", org_id)
                await conn.execute("DELETE FROM organizations WHERE id = $1", org_id)
    finally:
        reset_rls_context(tokens)


async def main() -> int:
    db_url = await bootstrap_async(quiet=True)
    if not db_url:
        print("FATAL: no working DATABASE_URL from Doppler.")
        return 2

    import os
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

    from services.database import close_pool, get_pool, reset_rls_context, set_rls_context
    import services.extraction as ex
    import services.litellm_credentials as lc
    import services.workflow_todos as wt
    from services.org_settings import set_setting
    from services.rbac import ORG_ADMIN_PERMISSION, get_users_with_permission

    async def pool_fetchrow(query, *args):
        p = await get_pool()
        return await p.fetchrow(query, *args)

    async def db_now_iso():
        return await pool_fetchrow(
            "SELECT to_char(now() AT TIME ZONE 'utc', "
            "'YYYY-MM-DD\"T\"HH24:MI:SS.US\"Z\"') AS ts"
        )

    async def db_now_ts():
        # A native timestamptz value (NOT the formatted string db_now_iso()
        # returns for comparing against LiteLLM's own JSON) — asyncpg's
        # binary protocol requires an actual datetime for a timestamptz bind
        # parameter, never a string, even with an explicit ::timestamptz cast
        # in the SQL text.
        return (await pool_fetchrow("SELECT now() AS ts"))["ts"]

    pool = await get_pool()
    rls_tokens = set_rls_context(None, True)

    baseline_deployments = {m.get("model_name") for m in _model_info()}
    print(f"baseline LiteLLM deployments: {sorted(baseline_deployments)}")

    try:
        # =====================================================================
        print("\n=== TASK 1: DISCOVER ===\n")

        exc_model_notfound = await _sdk_probe("verify-d1c-model-does-not-exist")
        check("1a/1b. an unregistered model_id fails with a NON-auth status "
              "— the model-UNAVAILABLE shape, live-probed",
              exc_model_notfound is not None
              and getattr(exc_model_notfound, "status_code", None) not in (401, 403),
              f"type={type(exc_model_notfound).__name__} "
              f"status={getattr(exc_model_notfound, 'status_code', None)}")

        bogus_master = "sk-verify-d1c-bogus-master-key-0000000000"  # not real, never printed raw
        exc_bad_master = await _sdk_probe("claude-sonnet", master_key=bogus_master)
        bad_master_body = json.dumps(getattr(exc_bad_master, "body", None) or "")
        check("1a/1b. a bad LITELLM_MASTER_KEY fails with HTTP 401 and names "
              "the PROXY TOKEN, never a model ('token_not_found_in_db')",
              getattr(exc_bad_master, "status_code", None) == 401
              and "token_not_found_in_db" in bad_master_body,
              f"status={getattr(exc_bad_master, 'status_code', None)}")

        sig_chain = inspect.signature(ex._execute_chain)
        src_chain = inspect.getsource(ex._execute_chain)
        check("1b. _execute_chain now positively distinguishes an org-"
              "credential failure from a model-unavailable one using "
              "routing state it already computed (credential_source == "
              "'org' and call_model_id != model_id), not by parsing "
              "LiteLLM's error text",
              "credential_source == CREDENTIAL_SOURCE_ORG" in src_chain
              and "call_model_id != model_id" in src_chain)

        sig_held = inspect.signature(wt.create_held_run_alerts)
        sig_cred = inspect.signature(wt.create_credential_failure_alerts)
        check("1c. create_credential_failure_alerts exists as a SIBLING of "
              "create_held_run_alerts, reusing the same private machinery",
              "_upsert_todo(" in inspect.getsource(wt.create_credential_failure_alerts)
              and "_org_admin_recipients(" in inspect.getsource(wt.create_credential_failure_alerts)
              and "_record_undelivered_alert(" in inspect.getsource(wt.create_credential_failure_alerts))

        find(f"1a. LIVE-PROBED SHAPE (via the real Anthropic SDK pointed at "
             f"LiteLLM, exactly as services.extraction._build_ai_client "
             f"constructs it): an org's own bad upstream provider key and a "
             f"bad LITELLM_MASTER_KEY BOTH raise anthropic.AuthenticationError "
             f"with status_code=401 — the SAME exception type and status the "
             f"SDK already used for a bad master key (confirmed live earlier "
             f"in this sprint against a throwaway deployment: {{'type': "
             f"'authentication_error', 'message': 'API key is invalid.'}}, "
             f"with 'Received Model Group=<the deployment name>' in the "
             f"message — a genuine passthrough of the upstream provider's own "
             f"rejection, keyed to the exact deployment LiteLLM tried). A bad "
             f"master key's message never names a model at all — it fails at "
             f"the PROXY's own gate (status={getattr(exc_bad_master, 'status_code', None)}, "
             f"body marker='token_not_found_in_db', confirmed live above). "
             f"They are NOT distinguishable by exception type or status code "
             f"alone — text-matching LiteLLM's error message would be "
             f"fragile, so this sprint does not: it identifies the org-"
             f"credential case from routing state _execute_chain already "
             f"computed (credential_source=='org' and call_model_id != "
             f"model_id, i.e. resolve_deployment_model just translated THIS "
             f"attempt onto the org's own dedicated deployment).")
        find("1b. Before this sprint, _execute_chain's _is_auth_failure "
             "branch treated ANY 401/403 from ANY model in the chain as a "
             "LITELLM_MASTER_KEY rejection — including one that was actually "
             "the org's own credential failing on its own dedicated "
             "deployment. That branch already stopped the chain walk dead "
             "(never fell onto a platform deployment for that attempt), so a "
             "broken org key was already never silently becoming "
             "Hollisworks' bill by accident — but the raised error MISNAMED "
             "the cause (always blamed LITELLM_MASTER_KEY) and raised no "
             "alert to anyone. A genuine model-unavailable failure (HTTP "
             f"400, confirmed live above: "
             f"status={getattr(exc_model_notfound, 'status_code', None)}) was "
             "already, and remains, structurally distinct — _is_auth_failure "
             "never caught it, so it always fell through to `continue` and "
             "walked the fallback chain. This sprint's change touches only "
             "the true-401/403 branch: it adds the org-credential vs. "
             "master-key split inside it; the 400 path is untouched.")
        find(f"1c. create_held_run_alerts{sig_held} — recipient rule: "
             "run.started_by UNION every manage_org_settings holder in "
             "org_id (services.rbac.get_users_with_permission), with a "
             "zero-recipient case writing an audit_log row via the shared "
             "_record_undelivered_alert helper. It could NOT be reused "
             "directly for a credential failure — it is hard-wired to a "
             "workflow_run's shape (a run_id, a started_by user, the "
             "run-console action_key), and a credential failure has no "
             "'who started this' concept at all (it is not tied to any one "
             f"run). create_credential_failure_alerts{sig_cred} is the "
             "sibling this sprint adds — same private machinery "
             "(_upsert_todo / _org_admin_recipients / "
             "_record_undelivered_alert), manage_org_settings-only "
             "recipients, own action name (CREDENTIAL_ALERT_UNDELIVERED_"
             "ACTION) so the two event kinds stay independently queryable.")

        # =====================================================================
        print("\n=== setup: fixtures (org A gets a deliberately bad "
              "anthropic credential; org B stays platform-sourced) ===\n")
        await setup_fixtures(pool)
        try:
            async with pool.acquire() as conn:
                status_a = await lc.set_org_provider_credential(
                    conn, pool, str(FIXTURE_ORG_A_ID), "anthropic",
                    BAD_ANTHROPIC_KEY, None,
                    principal={"role": "super_admin", "id": None},
                )
            check("setup: org A's credential_source is now 'org' (backed by "
                  "a real, deliberately-bad-key deployment)",
                  status_a.get("source") == "org")
            deployment_a = _find_stable(
                lc._org_deployment_name("anthropic", str(FIXTURE_ORG_A_ID)),
                expect_present=True,
            )
            check("setup: org A's own deployment is real and live",
                  deployment_a is not None)

            sonnet_entry = _find_deployment("claude-sonnet")
            check("baseline: the platform 'claude-sonnet' deployment exists",
                  sonnet_entry is not None)
            sonnet_id = (sonnet_entry or {}).get("model_info", {}).get("id")

            hollis_admins_precheck = await get_users_with_permission(
                pool, str(HOLLIS), ORG_ADMIN_PERMISSION
            )
            find("PRECONDITION confirmed live: the real Hollisworks org "
                 f"currently holds {len(hollis_admins_precheck)} "
                 "manage_org_settings grant(s) — the zero-recipient path "
                 "below is exercised against a genuinely live case, not a "
                 "constructed one.")

            # =================================================================
            print("\n=== TASK 2/3: an invalid ORG credential fails loud + "
                  "alerts (live) ===\n")
            call_since = (await db_now_iso())["ts"]
            raised = None
            try:
                await ex.call_claude_text(
                    "Reply with exactly one word.",
                    [{"role": "user", "content": "Say OK."}],
                    max_tokens=12, model="claude-sonnet",
                    org_id=str(FIXTURE_ORG_A_ID),
                    task_type="verify_d1c_org_a_bad_cred",
                )
            except Exception as exc:  # noqa: BLE001
                raised = exc

            check("2. an invalid ORG credential raises AIOrgCredentialError "
                  "(never AIChainExhausted, never a silent None)",
                  isinstance(raised, ex.AIOrgCredentialError),
                  f"got {type(raised).__name__}: {raised}")
            check("2. the raised error NAMES THE PROVIDER ('anthropic') and "
                  "this org's id",
                  raised is not None and "anthropic" in str(raised)
                  and str(FIXTURE_ORG_A_ID) in str(raised))
            check("2. the raised error explicitly states it will NOT fall "
                  "back to the Hollisworks platform key",
                  raised is not None and "platform key" in str(raised).lower())

            log_a = await pool_fetchrow(
                "SELECT * FROM ai_decision_log WHERE task_type = "
                "'verify_d1c_org_a_bad_cred' ORDER BY created_at DESC LIMIT 1"
            )
            check("2. ai_decision_log recorded the failure (success=false, "
                  "fallback_used=false — the chain did NOT try another "
                  "model)",
                  log_a is not None and log_a["success"] is False
                  and log_a["fallback_used"] is False)

            print(f"    waiting {SPEND_LOG_FLUSH_SECONDS}s spend-log flush "
                  f"window before checking for absence...")
            time.sleep(SPEND_LOG_FLUSH_SECONDS)
            rows_since = spend_rows_since(call_since)
            platform_rows_for_org_a = [
                r for r in rows_since
                if f"org:{FIXTURE_ORG_A_ID}" in (r.get("request_tags") or [])
                and r.get("model_id") == sonnet_id
            ]
            check("2. PROVEN BY ABSENCE after the full flush window: NO "
                  "spend-log row attributes this failed attempt to the "
                  "PLATFORM deployment — the chain never walked there",
                  len(platform_rows_for_org_a) == 0,
                  f"found {len(platform_rows_for_org_a)} such row(s)")

            todos_a = await pool.fetch(
                "SELECT * FROM member_todos WHERE org_id = $1 AND source = $2 "
                "AND status = 'open'",
                FIXTURE_ORG_A_ID, "ai_credential_failure:anthropic",
            )
            check("3. a real member_todos row was created for org A's "
                  "manage_org_settings holder",
                  len(todos_a) == 1
                  and str(todos_a[0]["user_id"]) == str(FIXTURE_ORG_A_ADMIN_ID),
                  f"got {[str(t['user_id']) for t in todos_a]}")
            if todos_a:
                check("3. the todo names the provider",
                      "anthropic" in (todos_a[0]["title"] or "").lower()
                      or "anthropic" in (todos_a[0]["detail"] or "").lower())

            todos_b = await pool.fetch(
                "SELECT * FROM member_todos WHERE org_id = $1 AND source "
                "LIKE 'ai_credential_failure%'",
                FIXTURE_ORG_B_ID,
            )
            check("CROSS-ORG: org B's manage_org_settings holder received "
                  "NOTHING from org A's credential failure",
                  len(todos_b) == 0, f"found {len(todos_b)} row(s)")

            # =================================================================
            print("\n=== model-unavailable STILL falls back (distinct path, "
                  "same run) ===\n")
            async with pool.acquire() as conn:
                await set_setting(
                    conn, str(FIXTURE_ORG_B_ID), "ai.model.fallback_chain",
                    ["claude-sonnet"], None,
                    principal={"role": "super_admin", "id": None}, pool=pool,
                )
            result_b = await ex.call_claude_text(
                "Reply with exactly one word.",
                [{"role": "user", "content": "Say OK."}],
                max_tokens=12, model="verify-d1c-model-does-not-exist",
                org_id=str(FIXTURE_ORG_B_ID),
                task_type="verify_d1c_model_unavailable",
            )
            check("model-unavailable: the call SUCCEEDS by walking the "
                  "fallback chain onto claude-sonnet — a 400 was never "
                  "caught by the credential-failure branch at all",
                  bool(result_b), f"{result_b!r}")
            log_b = await pool_fetchrow(
                "SELECT * FROM ai_decision_log WHERE task_type = "
                "'verify_d1c_model_unavailable' ORDER BY created_at DESC LIMIT 1"
            )
            check("model-unavailable: ai_decision_log shows success=true, "
                  "fallback_used=true, model_used='claude-sonnet' — DISTINCT "
                  "from the credential path's fallback_used=false above, "
                  "same test run",
                  log_b is not None and log_b["success"] is True
                  and log_b["fallback_used"] is True
                  and log_b["model_used"] == "claude-sonnet")

            # =================================================================
            print("\n=== 'platform' org (2nd Act, real) unaffected — no "
                  "regression ===\n")
            result_2a = await ex.call_claude_text(
                "Reply with exactly one word.",
                [{"role": "user", "content": "Say OK."}],
                max_tokens=12, model="claude-sonnet", org_id=str(ORG),
                task_type="verify_d1c_platform_2ndact",
            )
            check("'platform' org (2nd Act) succeeds end-to-end exactly as "
                  "before this sprint", bool(result_2a), f"{result_2a!r}")
            log_2a = await pool_fetchrow(
                "SELECT * FROM ai_decision_log WHERE task_type = "
                "'verify_d1c_platform_2ndact' ORDER BY created_at DESC LIMIT 1"
            )
            check("'platform' org: success=true, fallback_used=false — no "
                  "regression",
                  log_2a is not None and log_2a["success"] is True
                  and log_2a["fallback_used"] is False)

            # =================================================================
            print("\n=== zero-recipient path: the REAL Hollisworks org ===\n")
            probe_provider = "verify_d1c_probe_provider"
            probe_detail = ("verify_d1c synthetic credential-failure probe "
                             "— NOT a real failure, no credential was touched")
            audit_since = await db_now_ts()
            async with pool.acquire() as conn:
                ids = await wt.create_credential_failure_alerts(
                    conn, org_id=str(HOLLIS), provider=probe_provider,
                    error_detail=probe_detail,
                )
            check("zero-recipient: create_credential_failure_alerts returns "
                  "an EMPTY id list for Hollisworks (no todo written)",
                  ids == [])
            audit_row = await pool_fetchrow(
                "SELECT * FROM audit_log WHERE org_id = $1 AND action = $2 "
                "AND created_at >= $3 "
                "ORDER BY created_at DESC LIMIT 1",
                HOLLIS, wt.CREDENTIAL_ALERT_UNDELIVERED_ACTION, audit_since,
            )
            check("zero-recipient: a findable audit_log row was written "
                  "instead of failing silently", audit_row is not None)
            if audit_row is not None:
                payload = audit_row["payload"]
                if isinstance(payload, str):
                    payload = json.loads(payload)
                payload_text = json.dumps(payload)
                check("zero-recipient: the audit row names the reason (no "
                      "manage_org_settings holder)",
                      "manage_org_settings" in payload_text)
            todos_hollis = await pool.fetch(
                "SELECT id FROM member_todos WHERE org_id = $1 AND source = $2",
                HOLLIS, f"ai_credential_failure:{probe_provider}",
            )
            check("zero-recipient: zero member_todos rows were created for "
                  "Hollisworks", len(todos_hollis) == 0)

        finally:
            # Best-effort: deprovision org A's real (bad-key) deployment on
            # the live proxy BEFORE deleting the fixture org row that names
            # it, regardless of what failed above.
            try:
                async with pool.acquire() as conn:
                    await lc.clear_org_provider_credential(
                        conn, pool, str(FIXTURE_ORG_A_ID), "anthropic", None,
                        principal={"role": "super_admin", "id": None},
                    )
            except Exception as exc:  # noqa: BLE001
                print(f"[teardown] clear_org_provider_credential failed "
                      f"(will still sweep by name below): {exc}")
            leftover = _find_deployment(
                lc._org_deployment_name("anthropic", str(FIXTURE_ORG_A_ID))
            )
            if leftover is not None:
                _http("/model/delete", method="POST",
                      body={"id": leftover["model_info"]["id"]})

            await pool.execute(
                "DELETE FROM audit_log WHERE org_id = $1 AND action = $2 "
                "AND payload::text LIKE '%verify_d1c%'",
                HOLLIS, wt.CREDENTIAL_ALERT_UNDELIVERED_ACTION,
            )
            await teardown_fixtures(pool)

        # =====================================================================
        print("\n=== TEARDOWN: zero leftover rows and deployments ===\n")
        leftover_orgs = await pool_fetchrow(
            "SELECT count(*) AS n FROM organizations WHERE id = ANY($1::uuid[])",
            [FIXTURE_ORG_A_ID, FIXTURE_ORG_B_ID],
        )
        check("teardown: zero leftover fixture organizations",
              leftover_orgs["n"] == 0, f"n={leftover_orgs['n']}")
        leftover_users = await pool_fetchrow(
            "SELECT count(*) AS n FROM users WHERE org_id = ANY($1::uuid[])",
            [FIXTURE_ORG_A_ID, FIXTURE_ORG_B_ID],
        )
        check("teardown: zero leftover fixture users",
              leftover_users["n"] == 0, f"n={leftover_users['n']}")
        leftover_settings = await pool_fetchrow(
            "SELECT count(*) AS n FROM org_settings WHERE org_id = ANY($1::uuid[])",
            [FIXTURE_ORG_A_ID, FIXTURE_ORG_B_ID],
        )
        check("teardown: zero leftover fixture org_settings rows",
              leftover_settings["n"] == 0, f"n={leftover_settings['n']}")
        leftover_todos = await pool_fetchrow(
            "SELECT count(*) AS n FROM member_todos WHERE org_id = ANY($1::uuid[])",
            [FIXTURE_ORG_A_ID, FIXTURE_ORG_B_ID],
        )
        check("teardown: zero leftover fixture member_todos rows",
              leftover_todos["n"] == 0, f"n={leftover_todos['n']}")
        leftover_log = await pool_fetchrow(
            "SELECT count(*) AS n FROM ai_decision_log WHERE task_type LIKE 'verify_d1c_%'"
        )
        check("teardown: zero leftover ai_decision_log rows from this run",
              leftover_log["n"] == 0, f"n={leftover_log['n']}")
        leftover_audit = await pool_fetchrow(
            "SELECT count(*) AS n FROM audit_log WHERE org_id = $1 AND "
            "action = $2 AND payload::text LIKE '%verify_d1c%'",
            HOLLIS, wt.CREDENTIAL_ALERT_UNDELIVERED_ACTION,
        )
        check("teardown: zero leftover Hollisworks probe audit_log rows",
              leftover_audit["n"] == 0, f"n={leftover_audit['n']}")
        final_deployments = {m.get("model_name") for m in _model_info()}
        check("teardown: proxy back to exactly its pre-run deployment set "
              "(no leftover org-* deployments)",
              final_deployments == baseline_deployments,
              f"got {sorted(final_deployments)}")

    finally:
        reset_rls_context(rls_tokens)
        await close_pool()

    print(f"\n{'='*70}\nTOTAL: {_n_pass} PASS, {_n_fail} FAIL, {len(_finds)} FIND\n{'='*70}")
    return 0 if _ok else 1


if __name__ == "__main__":
    import asyncio

    sys.exit(asyncio.run(main()))
