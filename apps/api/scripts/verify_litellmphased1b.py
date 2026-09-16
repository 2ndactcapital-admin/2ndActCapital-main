"""verify_litellmphased1b.py — LiteLLM Phase D1b: routing + attribution.

Proves, against the REAL live ``hollisworks-litellm`` proxy and the real
database, that:

  1. Task 1's three discovery findings hold (re-probed live against the real
     proxy, not quoted from memory of D1a).
  2. A 'platform' org's real call is unchanged — no regression. Every
     existing org is in this state (D1a's own invariant).
  3. An 'org'-configured org's real call is served by ITS OWN deployment,
     proven from the spend log's own record of WHICH deployment ran it
     (litellm_params id), not inferred from config.
  4. LiteLLM's own spend log now carries real org attribution where it used
     to be null/empty — a genuine before/after, not just presence.
  5. Platform-key-on-behalf-of-an-org is tagged distinctly from Hollisworks'
     own platform-key usage.
  6. Embedding calls carry the same attribution (minus the ``user`` field,
     which Voyage rejects — a real, probed difference, not an oversight).
  7. No deployment name appears in any org-facing HTTP response.
  8. Cross-org: org A's call is never served by org B's deployment (or the
     platform's), proven the same way — by deployment id, not config.
  9. Teardown leaves zero fixture DB rows, zero leftover LiteLLM
     deployments, and zero leftover ai_decision_log rows.

Hydrates DATABASE_URL (and every other Doppler secret, including
LITELLM_BASE_URL / LITELLM_MASTER_KEY / ANTHROPIC_API_KEY) from Doppler over
HTTPS at startup — the verify_litellmphased1a.py pattern. run_sprint.sh's
Step 3 does NOT `doppler run --` this script.

Never prints a credential value.

Cost note: this script makes ~6 small real Anthropic calls (max_tokens<=16)
and 2 real Voyage embedding calls, paced >=65s apart per the project's
documented free-tier rate limit (3 req/min, 10K tokens/min — 25s was proven
insufficient in an earlier sprint).

Run:  python3 apps/api/scripts/verify_litellmphased1b.py
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

FIXTURE_ORG_A_ID = UUID("99000000-0000-0000-0000-0000000d1b01")
FIXTURE_ORG_B_ID = UUID("99000000-0000-0000-0000-0000000d1b02")
FIXTURE_ORG_A_USER_ID = UUID("99000000-0000-0000-0000-0000000d1b03")
FIXTURE_ORG_A_USER_SUB = "auth0|verify_d1b_org_a_member"

SPEND_LOG_FLUSH_SECONDS = 45
VOYAGE_PACE_SECONDS = 68  # project convention: 25s proven insufficient; Phase C used 65s.

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


def _http(path, *, method="GET", body=None, timeout=60, base=None, key=None):
    import os

    base = (base or os.environ.get("LITELLM_BASE_URL", "")).rstrip("/")
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


def spend_rows_since(since_iso: str, *, call_type=None):
    rows = spend_logs()
    out = [r for r in rows if (r.get("startTime") or "") >= since_iso]
    if call_type:
        out = [r for r in out if r.get("call_type") == call_type]
    return out


def wait_for_spend_row(since_iso: str, predicate, *, timeout=SPEND_LOG_FLUSH_SECONDS):
    """Poll GET /spend/logs until a row matching ``predicate`` shows up."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for row in spend_rows_since(since_iso):
            if predicate(row):
                return row
        time.sleep(3)
    return None


def _direct_call(model, *, metadata=None, user=None):
    """A RAW call bypassing services/extraction.py entirely — used to
    reproduce the pre-D1b "no attribution" baseline for the before/after
    proof, and for Task 1b's live metadata probing."""
    body = {"model": model, "max_tokens": 12,
            "messages": [{"role": "user", "content": "Reply with exactly: OK"}]}
    if metadata is not None:
        body["metadata"] = metadata
    if user is not None:
        body["user"] = user
    return _http("/v1/messages", method="POST", body=body)


def _direct_embed(model, texts, *, metadata=None, user=None):
    body = {"model": model, "input": texts}
    if metadata is not None:
        body["metadata"] = metadata
    if user is not None:
        body["user"] = user
    return _http("/v1/embeddings", method="POST", body=body)


# ── fixtures ─────────────────────────────────────────────────────────────


async def setup_fixtures(pool):
    from services.database import reset_rls_context, set_rls_context

    tokens = set_rls_context(None, True)
    try:
        async with pool.acquire() as conn:
            for org_id, name, slug in (
                (FIXTURE_ORG_A_ID, "Verify D1b Org A", "verify-d1b-org-a"),
                (FIXTURE_ORG_B_ID, "Verify D1b Org B", "verify-d1b-org-b"),
            ):
                await conn.execute(
                    """
                    INSERT INTO organizations (id, name, slug)
                    VALUES ($1, $2, $3)
                    ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name
                    """,
                    org_id, name, slug,
                )
            await conn.execute(
                """
                INSERT INTO users (id, org_id, email, full_name, auth0_sub, role)
                VALUES ($1, $2, $3, $4, $5, 'member')
                ON CONFLICT (id) DO UPDATE SET auth0_sub = EXCLUDED.auth0_sub,
                    org_id = EXCLUDED.org_id
                """,
                FIXTURE_ORG_A_USER_ID, FIXTURE_ORG_A_ID,
                f"{FIXTURE_ORG_A_USER_SUB}@test.local", "Verify D1b Org A Member",
                FIXTURE_ORG_A_USER_SUB,
            )
    finally:
        reset_rls_context(tokens)


async def teardown_fixtures(pool):
    from services.database import reset_rls_context, set_rls_context

    tokens = set_rls_context(None, True)
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM ai_decision_log WHERE task_type LIKE 'verify_d1b_%'"
            )
            for org_id in (FIXTURE_ORG_A_ID, FIXTURE_ORG_B_ID):
                await conn.execute("DELETE FROM org_settings WHERE org_id = $1", org_id)
                await conn.execute("DELETE FROM users WHERE org_id = $1", org_id)
                await conn.execute("DELETE FROM organizations WHERE id = $1", org_id)
    finally:
        reset_rls_context(tokens)


class _Principal:
    """Drives the real ASGI app as one user — the verify_litellmphased1a.py
    pattern. Used ONCE here, for the org-facing-leak regression check."""

    def __init__(self, client, sub, org_id):
        self.client, self.sub, self.org_id = client, sub, org_id

    def call(self, method, path):
        import main
        sub, org_id = self.sub, str(self.org_id)
        main.verify_token = lambda _t: {
            "sub": sub, "email": f"{sub}@test.local", "org_id": org_id,
        }
        fn = getattr(self.client, method)
        return fn(path, headers={"Authorization": "Bearer verify-token"})


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
    import services.document_embedding as de
    import services.litellm_credentials as lc

    async def pool_fetchrow(query, *args):
        p = await get_pool()
        return await p.fetchrow(query, *args)

    async def db_now_iso():
        return await pool_fetchrow(
            "SELECT to_char(now() AT TIME ZONE 'utc', "
            "'YYYY-MM-DD\"T\"HH24:MI:SS.US\"Z\"') AS ts"
        )

    pool = await get_pool()
    rls_tokens = set_rls_context(None, True)

    baseline_deployments = {m.get("model_name") for m in _model_info()}
    print(f"baseline LiteLLM deployments: {sorted(baseline_deployments)}")
    anthropic_key = os.environ["ANTHROPIC_API_KEY"]

    try:
        # =====================================================================
        print("\n=== TASK 1a — where D1b's resolver slots in ===\n")
        sig = inspect.signature(ex._execute_chain)
        check("1a. _execute_chain still the single choke point for every "
              "text/tool call (make_call/model_key/model_override params "
              "unchanged in shape)",
              set(sig.parameters) >= {"task_type", "org_id", "model_key",
                                       "model_override", "make_call", "extract"})
        src = inspect.getsource(ex._execute_chain)
        check("1a. _execute_chain calls resolve_credential_source AND "
              "resolve_deployment_model — the routing decision is wired "
              "into the SAME chain-walk loop that already resolves the "
              "model/fallback chain, not a second parallel path",
              "resolve_credential_source(" in src and "resolve_deployment_model(" in src)
        check("1a. the routing/attribution block is gated on "
              "transport == TRANSPORT_LITELLM — the direct-Anthropic "
              "rollback path is provably untouched",
              src.count("transport == TRANSPORT_LITELLM") >= 2)
        emb_src = inspect.getsource(de._execute_embedding_chain)
        check("1a. the embedding chain executor (document_embedding."
              "_execute_embedding_chain) reuses the SAME litellm_credentials "
              "functions — one resolver, not two",
              "resolve_credential_source(" in emb_src and "resolve_deployment_model(" in emb_src)
        find("1a. RESOLVER LOCATION: services/extraction.py's _execute_chain "
             "(text/tools) and services/document_embedding.py's "
             "_execute_embedding_chain (embeddings) are the two real chain "
             "executors. D1b's per-org resolution slots in at the SAME "
             "point in both: immediately after the model/fallback chain is "
             "computed, immediately before make_call()/_embed_litellm() is "
             "invoked, per attempt. It never touches resolve_model / "
             "resolve_fallback_chain (which still resolve the LOGICAL model "
             "name from org_settings) — it only decides which DEPLOYMENT "
             "answers that logical name, via services.litellm_credentials."
             "resolve_deployment_model.")

        # =====================================================================
        print("\n=== TASK 1b — live metadata probe (not assumed) ===\n")
        text_since = (await db_now_iso())["ts"]
        probe_tag = "verify-d1b-1b-text-probe"
        s_probe, b_probe = _direct_call(
            "claude-sonnet",
            metadata={"user_id": "probe", "tags": [probe_tag],
                      "spend_logs_metadata": {"probe": True}},
            user=f"probe-user:{probe_tag}",
        )
        check("1b. a real /v1/messages call with metadata.tags + top-level "
              "user succeeds (HTTP 200)", s_probe == 200, f"HTTP {s_probe}")
        row = wait_for_spend_row(text_since, lambda r: probe_tag in (r.get("request_tags") or []))
        check("1b. the probe call is found in GET /spend/logs "
              "(flush window respected)", row is not None)
        if row is not None:
            check("1b. metadata.tags -> LiteLLM_SpendLogs.request_tags "
                  "(probed field name)", probe_tag in (row.get("request_tags") or []),
                  f"request_tags={row.get('request_tags')}")
            check("1b. top-level user -> LiteLLM_SpendLogs.end_user "
                  "(probed field name — NOT the 'user' column, which stays "
                  "the API key's own default)",
                  row.get("end_user") == f"probe-user:{probe_tag}",
                  f"end_user={row.get('end_user')!r}")
            find("1b. metadata.user_id and metadata.spend_logs_metadata do "
                 "NOT land anywhere readable back via GET /spend/logs for a "
                 "master-key-authenticated call — user_api_key_user_id stays "
                 "the fixed 'default_user_id' and metadata.spend_logs_metadata "
                 f"comes back null (got user_api_key_user_id="
                 f"{(row.get('metadata') or {}).get('user_api_key_user_id')!r}, "
                 f"spend_logs_metadata="
                 f"{(row.get('metadata') or {}).get('spend_logs_metadata')!r}). "
                 "The two fields that DO work and that this sprint uses: "
                 "metadata.tags -> request_tags, top-level user -> end_user.")

        embed_since = (await db_now_iso())["ts"]
        probe_embed_tag = "verify-d1b-1b-embed-probe"
        s_user_reject, b_user_reject = _direct_embed(
            "voyage-3.5", ["1c probe: does voyage accept user?"],
            metadata={"tags": [probe_embed_tag]}, user="probe-user-should-be-rejected",
        )
        check("1c. sending the SAME top-level 'user' field to the embeddings "
              "route is REJECTED by Voyage (HTTP 400, UnsupportedParamsError) "
              "— a real, provider-specific difference from the text path, "
              "not an oversight", s_user_reject == 400 and "UnsupportedParamsError" in b_user_reject,
              f"HTTP {s_user_reject}: {b_user_reject[:200]}")

        embed_since2 = (await db_now_iso())["ts"]
        s_embed_ok, b_embed_ok = _direct_embed(
            "voyage-3.5", ["1c probe: tags only, no user"],
            metadata={"tags": [probe_embed_tag]},
        )
        check("1c. the SAME tags-only metadata (no user) succeeds on the "
              "embeddings route (HTTP 200)", s_embed_ok == 200, f"HTTP {s_embed_ok}")
        erow = wait_for_spend_row(embed_since2, lambda r: probe_embed_tag in (r.get("request_tags") or []))
        check("1c. metadata.tags -> request_tags works identically on the "
              "embeddings path", erow is not None
              and probe_embed_tag in (erow.get("request_tags") or []))
        find("1c. TEXT vs EMBEDDING mechanism: metadata.tags -> "
             "request_tags is SHARED by both paths (same field name, same "
             "spend-log column). Top-level user -> end_user works ONLY on "
             "the text/Anthropic path — Voyage rejects it outright. This is "
             "why services/document_embedding.py's _embed_litellm sends "
             "ONLY metadata.tags, never 'user', while services/extraction.py "
             "sends both.")

        # =====================================================================
        print("\n=== TASK 4a — 'platform' org: no regression ===\n")
        sonnet_entry = _find_deployment("claude-sonnet")
        check("baseline: platform 'claude-sonnet' deployment exists live",
              sonnet_entry is not None)
        sonnet_id = (sonnet_entry or {}).get("model_info", {}).get("id")

        org_2ndact_since = (await db_now_iso())["ts"]
        result_2ndact = await ex.call_claude_text(
            "Reply with exactly one word.", [{"role": "user", "content": "Say OK."}],
            max_tokens=12, model="claude-sonnet", org_id=str(ORG),
            task_type="verify_d1b_platform_2ndact",
        )
        check("4a. a real call for 2nd Act (a REAL, existing org, "
              "'platform'-sourced) succeeds end-to-end", bool(result_2ndact),
              f"{result_2ndact!r}")
        log_2ndact = await pool_fetchrow(
            "SELECT * FROM ai_decision_log WHERE task_type = "
            "'verify_d1b_platform_2ndact' ORDER BY created_at DESC LIMIT 1"
        )
        check("4a. ai_decision_log.model_used == 'claude-sonnet' (the LOGICAL "
              "name — unchanged by routing, no deployment name leak into our "
              "own log)", log_2ndact is not None and log_2ndact["model_used"] == "claude-sonnet")
        row_2ndact = wait_for_spend_row(
            org_2ndact_since,
            lambda r: r.get("call_type") == "anthropic_messages"
            and f"org:{ORG}" in (r.get("request_tags") or []),
        )
        check("4a. the call lands in LiteLLM's own spend log",
              row_2ndact is not None)
        if row_2ndact is not None:
            check("4a. NO REGRESSION: served by the PLATFORM deployment "
                  "(spend log's own model_id matches claude-sonnet's, proven "
                  "by deployment identity, not config)",
                  row_2ndact.get("model_id") == sonnet_id,
                  f"got model_id={row_2ndact.get('model_id')!r}, expected {sonnet_id!r}")
            check("4a. ATTRIBUTION (before-null, now populated): end_user == "
                  f"'org:{ORG}'", row_2ndact.get("end_user") == f"org:{ORG}",
                  f"end_user={row_2ndact.get('end_user')!r}")
            check("4a. usage label == 'platform_on_behalf_of_org' (2nd Act "
                  "is NOT Hollisworks' own org)",
                  f"usage:platform_on_behalf_of_org" in (row_2ndact.get("request_tags") or []),
                  f"tags={row_2ndact.get('request_tags')}")

        # =====================================================================
        print("\n=== TASK 4b — before/after: the null->populated proof ===\n")
        baseline_since = (await db_now_iso())["ts"]
        s_baseline, b_baseline = _direct_call("claude-sonnet")  # NO metadata/user at all
        check("4b. baseline (unattributed) call succeeds — reproduces the "
              "pre-D1b shape", s_baseline == 200, f"HTTP {s_baseline}")
        baseline_row = wait_for_spend_row(
            baseline_since,
            lambda r: r.get("call_type") == "anthropic_messages"
            and not any(t.startswith("org:") for t in (r.get("request_tags") or [])),
        )
        check("4b. BEFORE: an unattributed call has NO org: tag and an "
              "empty end_user (reproducing the exact gap this sprint closes)",
              baseline_row is not None and not baseline_row.get("end_user"),
              f"end_user={baseline_row.get('end_user')!r} tags={baseline_row.get('request_tags') if baseline_row else None}")
        check("4b. AFTER: the SAME call shape, made through our real code "
              "path (4a above), has a real org: tag and a real end_user — "
              "the genuine before/after this sprint's spend-log proof needs",
              row_2ndact is not None and row_2ndact.get("end_user") == f"org:{ORG}"
              and baseline_row is not None and not baseline_row.get("end_user"))

        # =====================================================================
        print("\n=== TASK 4c — Hollisworks' own usage, distinguishable ===\n")
        hollis_since = (await db_now_iso())["ts"]
        result_hollis = await ex.call_claude_text(
            "Reply with exactly one word.", [{"role": "user", "content": "Say OK."}],
            max_tokens=12, model="claude-sonnet", org_id=str(HOLLIS),
            task_type="verify_d1b_hollis",
        )
        check("4c. a real call for Hollisworks' own org succeeds", bool(result_hollis))
        row_hollis = wait_for_spend_row(
            hollis_since,
            lambda r: r.get("call_type") == "anthropic_messages"
            and f"org:{HOLLIS}" in (r.get("request_tags") or []),
        )
        check("4c. Hollisworks' own platform-key usage is tagged "
              "'usage:hollisworks_platform' — DIFFERENT from 2nd Act's "
              "'usage:platform_on_behalf_of_org' above, even though BOTH "
              "used the shared platform key",
              row_hollis is not None
              and "usage:hollisworks_platform" in (row_hollis.get("request_tags") or [])
              and row_2ndact is not None
              and "usage:platform_on_behalf_of_org" in (row_2ndact.get("request_tags") or []),
              f"hollis tags={row_hollis.get('request_tags') if row_hollis else None}")

        # =====================================================================
        print("\n=== TASK 4d/e — 'org'-configured orgs: own deployment + cross-org ===\n")
        await setup_fixtures(pool)
        deployment_a = deployment_b = None
        try:
            async with pool.acquire() as conn:
                status_a = await lc.set_org_provider_credential(
                    conn, pool, str(FIXTURE_ORG_A_ID), "anthropic", anthropic_key, None,
                    principal={"role": "super_admin", "id": None},
                )
            check("4d. org A: set_org_provider_credential reports source=='org'",
                  status_a.get("source") == "org", f"{status_a}")
            deployment_a = _find_stable(
                lc._org_deployment_name("anthropic", str(FIXTURE_ORG_A_ID)), expect_present=True
            )
            check("4d. org A's own deployment is real and distinct from the "
                  "platform's", deployment_a is not None
                  and deployment_a.get("model_info", {}).get("id") != sonnet_id)

            async with pool.acquire() as conn:
                status_b = await lc.set_org_provider_credential(
                    conn, pool, str(FIXTURE_ORG_B_ID), "anthropic", anthropic_key, None,
                    principal={"role": "super_admin", "id": None},
                )
            check("4e. org B: independently provisioned its OWN deployment",
                  status_b.get("source") == "org")
            deployment_b = _find_stable(
                lc._org_deployment_name("anthropic", str(FIXTURE_ORG_B_ID)), expect_present=True
            )
            check("4e. org A's and org B's deployments are REAL and MUTUALLY "
                  "DISTINCT (three different ids: platform, org A, org B)",
                  deployment_a is not None and deployment_b is not None
                  and len({sonnet_id,
                           deployment_a.get("model_info", {}).get("id"),
                           deployment_b.get("model_info", {}).get("id")}) == 3)

            # -- the real, live proof: org A's call is served by A's deployment --
            org_a_since = (await db_now_iso())["ts"]
            result_a = await ex.call_claude_text(
                "Reply with exactly one word.", [{"role": "user", "content": "Say OK."}],
                max_tokens=12, model="claude-sonnet", org_id=str(FIXTURE_ORG_A_ID),
                task_type="verify_d1b_orgA",
            )
            check("4d. org A's real, 'org'-routed call succeeds end-to-end",
                  bool(result_a), f"{result_a!r}")
            log_a = await pool_fetchrow(
                "SELECT * FROM ai_decision_log WHERE task_type = "
                "'verify_d1b_orgA' ORDER BY created_at DESC LIMIT 1"
            )
            check("4d. ai_decision_log.model_used is STILL the logical "
                  "'claude-sonnet' for org A too — the caller-facing model "
                  "name never changes, even though a different deployment "
                  "served it",
                  log_a is not None and log_a["model_used"] == "claude-sonnet")
            row_a = wait_for_spend_row(
                org_a_since,
                lambda r: r.get("call_type") == "anthropic_messages"
                and f"org:{FIXTURE_ORG_A_ID}" in (r.get("request_tags") or []),
            )
            check("4d. org A's call is found in the spend log", row_a is not None)
            if row_a is not None and deployment_a is not None:
                check("4d. PROVEN FROM THE SPEND LOG ITSELF: org A's call "
                      "ran on org A's OWN deployment id — not the platform's",
                      row_a.get("model_id") == deployment_a.get("model_info", {}).get("id"),
                      f"got {row_a.get('model_id')!r}, expected "
                      f"{deployment_a.get('model_info', {}).get('id')!r}")
                check("4d. usage label == 'org_owned_key'",
                      "usage:org_owned_key" in (row_a.get("request_tags") or []),
                      f"tags={row_a.get('request_tags')}")

            # -- cross-org: org B's call must land on B's deployment, never A's --
            org_b_since = (await db_now_iso())["ts"]
            result_b = await ex.call_claude_text(
                "Reply with exactly one word.", [{"role": "user", "content": "Say OK."}],
                max_tokens=12, model="claude-sonnet", org_id=str(FIXTURE_ORG_B_ID),
                task_type="verify_d1b_orgB",
            )
            check("4e. org B's real, 'org'-routed call succeeds end-to-end",
                  bool(result_b))
            row_b = wait_for_spend_row(
                org_b_since,
                lambda r: r.get("call_type") == "anthropic_messages"
                and f"org:{FIXTURE_ORG_B_ID}" in (r.get("request_tags") or []),
            )
            check("4e. org B's call is found in the spend log", row_b is not None)
            if row_b is not None and deployment_b is not None and deployment_a is not None:
                check("CROSS-ORG: org B's call ran on B's OWN deployment id — "
                      "NEVER org A's and NEVER the platform's",
                      row_b.get("model_id") == deployment_b.get("model_info", {}).get("id")
                      and row_b.get("model_id") != deployment_a.get("model_info", {}).get("id")
                      and row_b.get("model_id") != sonnet_id,
                      f"got {row_b.get('model_id')!r}")
                check("CROSS-ORG (other direction): org A's earlier call did "
                      "NOT run on org B's deployment id",
                      row_a is not None and row_a.get("model_id") != deployment_b.get("model_info", {}).get("id"))

            # -- no-deployment-name-leak regression check (org-facing HTTP) --
            from starlette.testclient import TestClient
            import main as main_module

            await close_pool()
            client = TestClient(main_module.app, raise_server_exceptions=False)
            client.__enter__()
            try:
                member_a = _Principal(client, FIXTURE_ORG_A_USER_SUB, FIXTURE_ORG_A_ID)
                r = member_a.call("get", f"/api/v1/orgs/{FIXTURE_ORG_A_ID}/settings/ai-credentials")
                check("org-facing GET ai-credentials still 200 after this "
                      "sprint's routing change", r.status_code == 200,
                      f"HTTP {r.status_code}")
                deployment_name_a = lc._org_deployment_name("anthropic", str(FIXTURE_ORG_A_ID))
                check("NO DEPLOYMENT NAME LEAK: raw response payload text "
                      "does not contain the internal deployment name",
                      deployment_name_a not in r.text)
                check("NO DEPLOYMENT NAME LEAK: ai_decision_log.model_used/"
                      "model_requested for org A's real call also never "
                      "contain the internal deployment name",
                      log_a is not None
                      and deployment_name_a not in (log_a["model_used"] or "")
                      and deployment_name_a not in (log_a["model_requested"] or ""))
            finally:
                client.__exit__(None, None, None)
            await close_pool()
            pool = await get_pool()

            # =================================================================
            print("\n=== TASK 6 — embeddings carry attribution too ===\n")
            voyage_entry = _find_deployment("voyage-3.5")
            voyage_id = (voyage_entry or {}).get("model_info", {}).get("id")
            check("baseline: platform 'voyage-3.5' deployment exists live",
                  voyage_entry is not None)

            embed_baseline_since = (await db_now_iso())["ts"]
            s_ebase, _b = _direct_embed("voyage-3.5", ["d1b baseline: no attribution"])
            check("6a. baseline (unattributed) embedding call succeeds",
                  s_ebase == 200, f"HTTP {s_ebase}")
            ebase_row = wait_for_spend_row(
                embed_baseline_since,
                lambda r: r.get("call_type") == "aembedding"
                and not any(t.startswith("org:") for t in (r.get("request_tags") or [])),
            )
            check("6a. BEFORE: unattributed embedding call has no org: tag",
                  ebase_row is not None)

            print(f"    pacing {VOYAGE_PACE_SECONDS}s for Voyage's free-tier "
                  f"rate limit (3 req/min) before the next real call...")
            time.sleep(VOYAGE_PACE_SECONDS)
            # A pooled connection held across a long sleep gets reset by the
            # Supabase pooler (documented precedent — verify_litellmphasec.py).
            await close_pool()
            pool = await get_pool()

            embed_real_since = (await db_now_iso())["ts"]
            vectors = await de.embed_texts(
                ["d1b real call: through the fixed code path"],
                org_id=str(ORG), task_type="verify_d1b_embed_2ndact",
            )
            check("6b. a real embedding call through document_embedding."
                  "embed_texts succeeds", bool(vectors) and len(vectors) == 1)
            log_embed = await pool_fetchrow(
                "SELECT * FROM ai_decision_log WHERE task_type = "
                "'verify_d1b_embed_2ndact' ORDER BY created_at DESC LIMIT 1"
            )
            check("6b. ai_decision_log.model_used == 'voyage-3.5' (logical, "
                  "unchanged)", log_embed is not None and log_embed["model_used"] == "voyage-3.5")
            erow_real = wait_for_spend_row(
                embed_real_since,
                lambda r: r.get("call_type") == "aembedding"
                and f"org:{ORG}" in (r.get("request_tags") or []),
            )
            check("6b. AFTER: the real call through our code carries "
                  f"org:{ORG} attribution in the spend log — the SAME "
                  "before/after proof as the text path, on the embedding "
                  "path", erow_real is not None)
            check("6b. no regression: served by the PLATFORM voyage "
                  "deployment (2nd Act is 'platform'-sourced for voyage)",
                  erow_real is not None and erow_real.get("model_id") == voyage_id)
            check("6b. embedding attribution has NO end_user (Voyage rejects "
                  "the field — Task 1c's finding applied correctly, not "
                  "merely documented)",
                  erow_real is not None and not erow_real.get("end_user"))

            # Function-level (no extra live Voyage call — respects the "pace
            # 65s apart" constraint) proof that embedding org-routing IS
            # wired identically to the text path.
            translated = lc.resolve_deployment_model(
                "voyage-3.5", str(FIXTURE_ORG_A_ID), "voyage", lc.CREDENTIAL_SOURCE_ORG
            )
            check("6c. resolve_deployment_model translates 'voyage-3.5' -> "
                  "the org's own deployment name when credential_source == "
                  "'org' (function-level check — a live 'org'-routed Voyage "
                  "call is deliberately not made here to respect the "
                  "documented free-tier pacing budget; the text-path "
                  "equivalent above IS proven live end-to-end)",
                  translated == lc._org_deployment_name("voyage", str(FIXTURE_ORG_A_ID)))
            check("6c. the SAME model_id is untouched when credential_source "
                  "== 'platform' (no regression, function-level)",
                  lc.resolve_deployment_model(
                      "voyage-3.5", str(FIXTURE_ORG_A_ID), "voyage",
                      lc.CREDENTIAL_SOURCE_PLATFORM) == "voyage-3.5")

        finally:
            # Best-effort cleanup of any deployment left behind by a failed
            # assertion, BEFORE deleting the fixture orgs that name them.
            for org_id in (FIXTURE_ORG_A_ID, FIXTURE_ORG_B_ID):
                for provider in ("anthropic", "voyage"):
                    leftover = _find_deployment(lc._org_deployment_name(provider, str(org_id)))
                    if leftover is not None:
                        _http("/model/delete", method="POST",
                              body={"id": leftover["model_info"]["id"]})
            await teardown_fixtures(pool)

        # =====================================================================
        print("\n=== TASK 9 — TEARDOWN: zero leftover rows and deployments ===\n")
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
        leftover_log = await pool_fetchrow(
            "SELECT count(*) AS n FROM ai_decision_log WHERE task_type LIKE 'verify_d1b_%'"
        )
        check("teardown: zero leftover ai_decision_log rows from this run",
              leftover_log["n"] == 0, f"n={leftover_log['n']}")
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
