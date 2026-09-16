"""verify_litellmphased1a.py — LiteLLM Phase D1a: per-org credential storage.

Proves, against the REAL live `hollisworks-litellm` proxy and the real
database, that:

  1. Task 1's three discovery findings hold (re-probed live, not quoted from
     memory of a prior sprint).
  2. Every existing org still resolves ai.credential_source.* to 'platform' —
     no behavioural change for anyone who has not opted in.
  3. Supplying a test org's own provider key creates a REAL, distinct LiteLLM
     deployment (confirmed via the proxy's own admin API, not just "the call
     didn't error").
  4. Removing that key removes the deployment (same discipline).
  5. No org-facing HTTP response — read or write — ever contains the internal
     deployment name, at any point in the lifecycle.
  6. Cross-org isolation: two orgs' deployments coexist independently, and
     org B cannot read, write, or clear org A's credential (proven on the
     IDENTICAL request org A's own admin succeeds on).
  7. A non-admin member of org A is refused the identical request org A's
     admin succeeds on (permission gate proven both ways).
  8. Teardown leaves zero fixture rows and zero leftover LiteLLM deployments.

Hydrates DATABASE_URL (and every other Doppler secret, including
LITELLM_BASE_URL / LITELLM_MASTER_KEY / ANTHROPIC_API_KEY) from Doppler over
HTTPS at startup — the verify_orgadminrole.py pattern. run_sprint.sh's Step 3
does NOT `doppler run --` this script.

Never prints a credential value. Every assertion involving a raw key checks
membership/absence of the literal string, never prints it.

Run:  python3 apps/api/scripts/verify_litellmphased1a.py
"""
from __future__ import annotations

import asyncio
import json
import pathlib
import sys
import traceback
from uuid import UUID

HERE = pathlib.Path(__file__).resolve()
sys.path.insert(0, str(HERE.parent))
from _db_bootstrap import bootstrap_async  # noqa: E402

HEADERS = {"Authorization": "Bearer verify-token"}

ORG = UUID("00000000-0000-0000-0000-000000000001")          # 2nd Act Capital (real)
HOLLIS = UUID("bb347258-8f28-4f49-8cc9-e29ccad82884")        # Hollisworks (real)

# This script's own fixtures — created and fully torn down here.
FIXTURE_ORG_A_ID = UUID("99000000-0000-0000-0000-0000000d1a01")
FIXTURE_ORG_B_ID = UUID("99000000-0000-0000-0000-0000000d1a02")
FIXTURE_ORG_A_ADMIN_ID = UUID("99000000-0000-0000-0000-0000000d1a03")
FIXTURE_ORG_B_ADMIN_ID = UUID("99000000-0000-0000-0000-0000000d1a04")
FIXTURE_ORG_A_NONADMIN_ID = UUID("99000000-0000-0000-0000-0000000d1a05")
FIXTURE_ORG_A_ADMIN_SUB = "auth0|verify_d1a_org_a_admin"
FIXTURE_ORG_B_ADMIN_SUB = "auth0|verify_d1a_org_b_admin"
FIXTURE_ORG_A_NONADMIN_SUB = "auth0|verify_d1a_org_a_nonadmin"

FAKE_ORG_A_KEY = "sk-ant-fake-verify-d1a-org-a-test-key-do-not-use"
FAKE_ORG_B_KEY = "sk-ant-fake-verify-d1a-org-b-test-key-do-not-use"

_ok = True
_n_pass = 0
_n_fail = 0
_finds = []


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
    """Drives the real ASGI app as one user — the verify_orgadminrole.py
    pattern. ``sub`` stubs main.verify_token's returned JWT claims.

    ``org_id`` is ALSO stubbed into the claims (the real ``org_id`` custom
    claim name, from routers.entities.ORG_ID_CLAIMS) — real Auth0 access
    tokens for this app's API audience carry no org claim at all, and
    ``org_id_from_claims`` falls back to DEFAULT_ORG_ID (2nd Act) when none is
    present (see routers/entities.py's own docstring on this exact bug this
    app already fixed for real logins). Without this, EVERY request here
    would resolve its RLS org_id GUC to 2nd Act regardless of which fixture
    org the caller's own users row belongs to — which would make every write
    against a fixture org fail RLS (wrong org GUC), not because of a real
    permission problem.
    """

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


async def setup_fixtures(pool):
    from services.database import reset_rls_context, set_rls_context
    from services.rbac import ensure_permission, ensure_role, grant_org_admin

    tokens = set_rls_context(None, True)
    try:
        async with pool.acquire() as conn:
            for org_id, name, slug in (
                (FIXTURE_ORG_A_ID, "Verify D1a Org A", "verify-d1a-org-a"),
                (FIXTURE_ORG_B_ID, "Verify D1a Org B", "verify-d1a-org-b"),
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
                (FIXTURE_ORG_A_NONADMIN_ID, FIXTURE_ORG_A_ID, FIXTURE_ORG_A_NONADMIN_SUB, "Org A NonAdmin"),
            ):
                await conn.execute(
                    """
                    INSERT INTO users (id, org_id, email, full_name, auth0_sub, role)
                    VALUES ($1, $2, $3, $4, $5, 'member')
                    ON CONFLICT (id) DO UPDATE SET auth0_sub = EXCLUDED.auth0_sub,
                        org_id = EXCLUDED.org_id
                    """,
                    user_id, org_id, f"{sub}@test.local", f"Verify D1a {label}", sub,
                )
            await grant_org_admin(conn, FIXTURE_ORG_A_ADMIN_ID, FIXTURE_ORG_A_ID)
            await grant_org_admin(conn, FIXTURE_ORG_B_ADMIN_ID, FIXTURE_ORG_B_ID)

            # The non-admin fixture MUST hold a real, non-empty role that does
            # NOT include manage_org_settings — has_permission default-ALLOWS
            # a user with ZERO user_roles grants (the documented single-admin
            # bootstrap posture), so a role-less fixture would prove nothing
            # about the refusal path (the exact lesson verify_orgadminrole.py
            # already learned building its own non-admin fixture).
            harmless_perm_id = await ensure_permission(
                conn, "view_dashboard", "dashboard", "view"
            )
            nonadmin_role_id = await ensure_role(
                conn, FIXTURE_ORG_A_ID, "verify_d1a_nonadmin",
                "verify_litellmphased1a fixture — real role, no manage_org_settings",
            )
            await conn.execute(
                "INSERT INTO role_permissions (role_id, permission_id) VALUES ($1, $2) "
                "ON CONFLICT DO NOTHING",
                nonadmin_role_id, harmless_perm_id,
            )
            await conn.execute(
                "INSERT INTO user_roles (user_id, role_id) VALUES ($1, $2) ON CONFLICT DO NOTHING",
                FIXTURE_ORG_A_NONADMIN_ID, nonadmin_role_id,
            )
    finally:
        reset_rls_context(tokens)


async def teardown_fixtures(pool):
    from services.database import reset_rls_context, set_rls_context

    tokens = set_rls_context(None, True)
    try:
        async with pool.acquire() as conn:
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


# ── LiteLLM admin API — direct HTTP, same shape as services.litellm_credentials ──


def _litellm_http(path, *, method="GET", body=None, timeout=60):
    import os
    import urllib.error
    import urllib.request

    base = os.environ.get("LITELLM_BASE_URL", "").rstrip("/")
    key = os.environ.get("LITELLM_MASTER_KEY", "")
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
    s, b = _litellm_http("/model/info")
    if s != 200:
        raise RuntimeError(f"GET /model/info -> {s}: {b[:300]}")
    return json.loads(b).get("data", [])


def _find(model_name):
    for m in _model_info():
        if m.get("model_name") == model_name:
            return m
    return None


def _find_stable(model_name, *, expect_present: bool, timeout=12.0, interval=1.0):
    """Poll GET /model/info until it agrees with ``expect_present``, or the
    window expires. Observed live (not assumed): a read immediately after a
    write occasionally returns stale data — the same class of propagation lag
    already documented for LiteLLM_SpendLogs elsewhere in this project, just
    on a much shorter, metadata-only timescale. Returns whatever the last poll
    saw, so a genuine failure still reports the real final state."""
    import time as _t
    deadline = _t.monotonic() + timeout
    result = _find(model_name)
    while (result is not None) != expect_present and _t.monotonic() < deadline:
        _t.sleep(interval)
        result = _find(model_name)
    return result


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

    from services.database import get_pool, reset_rls_context, set_rls_context
    import services.litellm_credentials as lc
    import services.org_settings as org_settings_mod

    pool = await get_pool()

    baseline_deployments = {m.get("model_name") for m in _model_info()}
    print(f"baseline LiteLLM deployments: {sorted(baseline_deployments)}")

    try:
        # ══════════════════════════════════════════════════════════════
        print("\n=== TASK 1a: literal api_key values, probed live ===\n")
        anth = os.environ["ANTHROPIC_API_KEY"]
        probe_name = "verify-d1a-1a-literal-probe"
        s_new, b_new = _litellm_http("/model/new", method="POST", body={
            "model_name": probe_name,
            "litellm_params": {"model": "anthropic/claude-haiku-4-5-20251001",
                                "api_key": anth},
        })
        check("1a. POST /model/new accepts a LITERAL api_key value (not just "
              "os.environ/ indirection) -> HTTP 200", s_new == 200, f"HTTP {s_new}")

        probe_entry = _find(probe_name)
        check("1a. the literal-key deployment persists and is readable via "
              "GET /model/info", probe_entry is not None)
        probe_lp = (probe_entry or {}).get("litellm_params", {})
        check("1a. GET /model/info NEVER echoes api_key back, for a literal "
              "value either (redacted at read time, same as os.environ/ "
              "indirection)", "api_key" not in probe_lp, f"keys={list(probe_lp)}")

        s_call, b_call = _litellm_http("/v1/chat/completions", method="POST", body={
            "model": probe_name,
            "messages": [{"role": "user", "content": "Reply with exactly one word: OK"}],
            "max_tokens": 8,
        })
        real_reply = None
        if s_call == 200:
            real_reply = json.loads(b_call).get("choices", [{}])[0].get("message", {}).get("content")
        check("1a. a REAL call through the literal-key deployment succeeds "
              "(HTTP 200, genuine model output) — proves the literal value is "
              "functionally used as the outbound credential, not merely "
              "accepted and ignored", s_call == 200 and bool(real_reply),
              f"HTTP {s_call} reply={real_reply!r}")
        find("1a. CENTRAL FINDING: POST /model/new's litellm_params.api_key "
             "accepts a literal credential value. LiteLLM encrypts it at rest "
             "(LITELLM_SALT_KEY) and never exposes it again through any read "
             "endpoint. This is the mechanism Task 3 provisioning uses — an "
             "org's own key is passed literally, once, and never stored in "
             "our own database.")

        if probe_entry is not None:
            s_del, b_del = _litellm_http(
                "/model/delete", method="POST",
                body={"id": probe_entry["model_info"]["id"]},
            )
            check("1a. probe deployment cleaned up immediately", s_del == 200,
                  f"HTTP {s_del}")
        check("1a. proxy back to exactly its pre-probe deployment set",
              {m.get("model_name") for m in _model_info()} == baseline_deployments)

        # ══════════════════════════════════════════════════════════════
        print("\n=== TASK 1b: org_settings credential-source shape ===\n")
        for provider in ("anthropic", "voyage"):
            key = lc.credential_source_key(provider)
            check(f"1b. DEFAULT_SETTINGS[{key!r}] == 'platform' (code-level "
                  f"fallback, no schema change)",
                  org_settings_mod.DEFAULT_SETTINGS.get(key) == "platform")

        try:
            await org_settings_mod._validate_setting(None, None, "ai.credential_source.anthropic", "bogus-value")
            check("1b. _validate_setting rejects an invalid credential source "
                  "value", False, "did not raise")
        except org_settings_mod.SettingsValidationError:
            check("1b. _validate_setting rejects an invalid credential source "
                  "value ('bogus-value')", True)

        try:
            await org_settings_mod._validate_setting(None, None, "ai.credential_source.openai", "org")
            check("1b. _validate_setting rejects an unsupported provider", False,
                  "did not raise")
        except org_settings_mod.SettingsValidationError:
            check("1b. _validate_setting rejects an unsupported provider "
                  "('openai' has no platform deployment to mirror)", True)

        await org_settings_mod._validate_setting(None, None, "ai.credential_source.anthropic", "org")
        check("1b. _validate_setting accepts a genuinely valid value ('org') "
              "for a genuinely known provider — the negative checks above "
              "prove something real", True)
        find("1b. ai.credential_source.<provider> fits org_settings' existing "
             "jsonb column with NO schema change, validated with the exact "
             "same enum-precedent shape as ai.embedding.provider.")

        # ══════════════════════════════════════════════════════════════
        print("\n=== TASK 1c: exact live payload behind claude-sonnet / voyage-3.5 ===\n")
        sonnet_entry = _find("claude-sonnet")
        voyage_entry = _find("voyage-3.5")
        check("1c. claude-sonnet deployment exists live", sonnet_entry is not None)
        check("1c. voyage-3.5 deployment exists live", voyage_entry is not None)
        sonnet_model = (sonnet_entry or {}).get("litellm_params", {}).get("model")
        voyage_model = (voyage_entry or {}).get("litellm_params", {}).get("model")
        check("1c. claude-sonnet's litellm_params.model == "
              "'anthropic/claude-sonnet-4-6'",
              sonnet_model == "anthropic/claude-sonnet-4-6", f"got {sonnet_model!r}")
        check("1c. voyage-3.5's litellm_params.model == 'voyage/voyage-3.5'",
              voyage_model == "voyage/voyage-3.5", f"got {voyage_model!r}")
        find("1c. GET /model/info never returns litellm_params.api_key for "
             "EITHER existing deployment (confirmed above this also holds for "
             "a literal value) — so this script cannot visually distinguish "
             "os.environ/ indirection from a literal value on these two "
             "pre-existing rows. docs/LITELLM_INTEGRATION_DESIGN_V1.md §14.2 "
             "already recorded voyage-3.5's real creation payload directly "
             "(\"api_key\": \"os.environ/VOYAGE_API_KEY\"), which is the "
             "source for that half of this finding; the .model field for "
             "both is confirmed live above.")

        # ══════════════════════════════════════════════════════════════
        print("\n=== TASK 2: every REAL, existing org still resolves 'platform' ===\n")
        tokens = set_rls_context(None, True)
        try:
            async with pool.acquire() as conn:
                for real_org, org_label in ((ORG, "2nd Act"), (HOLLIS, "Hollisworks")):
                    for provider in ("anthropic", "voyage"):
                        key = lc.credential_source_key(provider)
                        row = await conn.fetchrow(
                            "SELECT setting_value FROM org_settings WHERE org_id = $1 AND setting_key = $2",
                            real_org, key,
                        )
                        check(f"2. {org_label}: no org_settings row for {key} "
                              f"(genuinely defaulted, not coincidentally 'platform')",
                              row is None)
                        value = await org_settings_mod.get_setting(conn, real_org, key)
                        check(f"2. {org_label}: get_setting({key}) == 'platform'",
                              value == "platform", f"got {value!r}")
        finally:
            reset_rls_context(tokens)

        # ══════════════════════════════════════════════════════════════
        print("\n=== TASK 3/4: real provisioning, real proof ===\n")
        await setup_fixtures(pool)
        try:
            from starlette.testclient import TestClient
            import main as main_module
            from services.database import close_pool

            await close_pool()
            client = TestClient(main_module.app, raise_server_exceptions=False)
            client.__enter__()
            try:
                admin_a = _Principal(client, FIXTURE_ORG_A_ADMIN_SUB, FIXTURE_ORG_A_ID)
                admin_b = _Principal(client, FIXTURE_ORG_B_ADMIN_SUB, FIXTURE_ORG_B_ID)
                nonadmin_a = _Principal(client, FIXTURE_ORG_A_NONADMIN_SUB, FIXTURE_ORG_A_ID)

                # -- baseline reads: both orgs show 'platform' before anything --
                r = admin_a.call("get", f"/api/v1/orgs/{FIXTURE_ORG_A_ID}/settings/ai-credentials")
                check("3. org A admin: GET ai-credentials -> 200 before any key "
                      "supplied", r.status_code == 200, f"HTTP {r.status_code}")
                sources_a = {c["provider"]: c["source"] for c in r.json().get("credentials", [])}
                check("3. org A: both providers report 'platform' before any "
                      "key is supplied", sources_a == {"anthropic": "platform", "voyage": "platform"},
                      f"{sources_a}")

                # -- reads are open to any org member (same convention as the
                #    rest of this router — GET /orgs/{id}/settings) — a
                #    non-admin CAN read their own org's credential source,
                #    it's just 'org'/'platform', not a secret. The real
                #    permission gate is on the WRITE side, proven below. --
                r_na = nonadmin_a.call("get", f"/api/v1/orgs/{FIXTURE_ORG_A_ID}/settings/ai-credentials")
                check("7. non-admin member of org A: GET ai-credentials -> 200 "
                      "(reads are open to any org member, same as the rest of "
                      "this settings router)", r_na.status_code == 200,
                      f"HTTP {r_na.status_code}")

                # -- org A supplies its own key --
                r = admin_a.call("put",
                    f"/api/v1/orgs/{FIXTURE_ORG_A_ID}/settings/ai-credentials/anthropic",
                    body={"api_key": FAKE_ORG_A_KEY})
                check("3. org A admin: PUT ai-credentials/anthropic -> 200",
                      r.status_code == 200, f"HTTP {r.status_code} body={r.text[:300]}")
                check("3. response reports source == 'org'",
                      (r.json() or {}).get("source") == "org", f"{r.json()}")

                deployment_name_a = lc._org_deployment_name("anthropic", FIXTURE_ORG_A_ID)
                check("5. PUT response body contains NO deployment name "
                      f"({deployment_name_a!r} never appears)",
                      deployment_name_a not in r.text)

                entry_a = _find_stable(deployment_name_a, expect_present=True)
                check("3. a REAL, distinct deployment now exists on the live "
                      "proxy for org A, confirmed by reading the proxy's own "
                      "admin API back (GET /model/info)", entry_a is not None)
                if entry_a is not None:
                    check("3. org A's deployment mirrors the platform "
                          "claude-sonnet upstream model string",
                          entry_a.get("litellm_params", {}).get("model") == sonnet_model,
                          f"got {entry_a.get('litellm_params', {}).get('model')!r}")
                    check("3. org A's deployment is NOT the platform's own "
                          "model_id — a genuinely separate deployment, not a "
                          "mutation of the shared one",
                          entry_a.get("model_info", {}).get("id") != (sonnet_entry or {}).get("model_info", {}).get("id"))

                # -- non-admin still refused, now that state has actually changed --
                r_na2 = nonadmin_a.call("put",
                    f"/api/v1/orgs/{FIXTURE_ORG_A_ID}/settings/ai-credentials/anthropic",
                    body={"api_key": "sk-ant-nonadmin-should-never-land"})
                check("7. non-admin member of org A: PUT ai-credentials -> 403 "
                      "(identical request org A's admin just got 200 on)",
                      r_na2.status_code == 403, f"HTTP {r_na2.status_code}")

                # -- org B provisions its OWN, independent deployment --
                r_b = admin_b.call("put",
                    f"/api/v1/orgs/{FIXTURE_ORG_B_ID}/settings/ai-credentials/anthropic",
                    body={"api_key": FAKE_ORG_B_KEY})
                check("6. org B admin: PUT own ai-credentials/anthropic -> 200 "
                      "(independent of org A)", r_b.status_code == 200,
                      f"HTTP {r_b.status_code}")
                deployment_name_b = lc._org_deployment_name("anthropic", FIXTURE_ORG_B_ID)
                entry_b = _find_stable(deployment_name_b, expect_present=True)
                check("6. org B's deployment is REAL and DISTINCT from org A's "
                      "(different internal names, different model_ids — two "
                      "orgs' own keys coexist independently)",
                      entry_b is not None and entry_a is not None
                      and entry_b.get("model_info", {}).get("id") != entry_a.get("model_info", {}).get("id"))

                # -- CROSS-ORG: org B cannot read/write/clear org A's credential,
                #    on the IDENTICAL requests org A's own admin succeeds on --
                r_cross_read = admin_b.call("get", f"/api/v1/orgs/{FIXTURE_ORG_A_ID}/settings/ai-credentials")
                check("6. CROSS-ORG: org B admin -> GET org A's ai-credentials "
                      "-> 403", r_cross_read.status_code == 403, f"HTTP {r_cross_read.status_code}")

                r_cross_write = admin_b.call("put",
                    f"/api/v1/orgs/{FIXTURE_ORG_A_ID}/settings/ai-credentials/anthropic",
                    body={"api_key": "sk-ant-org-b-should-never-touch-org-a"})
                check("6. CROSS-ORG: org B admin -> PUT org A's ai-credentials "
                      "-> 403 (identical request org A's own admin succeeded "
                      "on above)", r_cross_write.status_code == 403,
                      f"HTTP {r_cross_write.status_code}")

                r_cross_delete = admin_b.call("delete", f"/api/v1/orgs/{FIXTURE_ORG_A_ID}/settings/ai-credentials/anthropic")
                check("6. CROSS-ORG: org B admin -> DELETE org A's "
                      "ai-credentials -> 403", r_cross_delete.status_code == 403,
                      f"HTTP {r_cross_delete.status_code}")

                # Confirm org A's deployment/setting are UNTOUCHED by the
                # refused cross-org attempts above.
                entry_a_after_cross = _find(deployment_name_a)
                check("6. org A's deployment still exists, unaffected by org "
                      "B's refused cross-org attempts",
                      entry_a_after_cross is not None
                      and entry_a_after_cross.get("model_info", {}).get("id") == entry_a.get("model_info", {}).get("id"))

                # -- remove org B's key first, confirm ONLY org B is affected --
                r_del_b = admin_b.call("delete", f"/api/v1/orgs/{FIXTURE_ORG_B_ID}/settings/ai-credentials/anthropic")
                check("4. org B admin: DELETE own ai-credentials -> 200",
                      r_del_b.status_code == 200, f"HTTP {r_del_b.status_code}")
                check("4. org B's deployment is really gone (proxy admin API "
                      "read-back)",
                      _find_stable(deployment_name_b, expect_present=False) is None)
                check("6. org A's deployment is STILL there — org B's removal "
                      "did not touch org A's own deployment",
                      _find(deployment_name_a) is not None)

                # -- remove org A's key --
                r_del_a = admin_a.call("delete", f"/api/v1/orgs/{FIXTURE_ORG_A_ID}/settings/ai-credentials/anthropic")
                check("4. org A admin: DELETE ai-credentials/anthropic -> 200",
                      r_del_a.status_code == 200, f"HTTP {r_del_a.status_code}")
                check("4. response reports source == 'platform' again",
                      (r_del_a.json() or {}).get("source") == "platform", f"{r_del_a.json()}")
                check("5. DELETE response body contains NO deployment name",
                      deployment_name_a not in r_del_a.text)
                check("4. org A's deployment is really gone, confirmed by "
                      "reading the proxy's own admin API back",
                      _find_stable(deployment_name_a, expect_present=False) is None)

                r_final = admin_a.call("get", f"/api/v1/orgs/{FIXTURE_ORG_A_ID}/settings/ai-credentials")
                final_sources = {c["provider"]: c["source"] for c in r_final.json().get("credentials", [])}
                check("4. org A is fully back to 'platform' for anthropic",
                      final_sources.get("anthropic") == "platform", f"{final_sources}")
                check("5. final GET response body contains NO deployment name "
                      "anywhere in the payload (grepped the real text, not a "
                      "parsed dict)", deployment_name_a not in r_final.text)

                # -- also confirm the general settings endpoint never leaks it --
                r_general = admin_a.call("get", f"/api/v1/orgs/{FIXTURE_ORG_A_ID}/settings?detail=true")
                check("5. GET /orgs/{id}/settings (general, detail=true) "
                      "contains NO deployment name either",
                      deployment_name_a not in r_general.text)
            finally:
                client.__exit__(None, None, None)

            await close_pool()
            pool = await get_pool()
        finally:
            # Best-effort cleanup of any deployment left behind by a failed
            # assertion above, BEFORE deleting the fixture orgs/users that
            # name them.
            for org_id in (FIXTURE_ORG_A_ID, FIXTURE_ORG_B_ID):
                for provider in ("anthropic", "voyage"):
                    leftover = _find(lc._org_deployment_name(provider, org_id))
                    if leftover is not None:
                        _litellm_http("/model/delete", method="POST",
                                       body={"id": leftover["model_info"]["id"]})
            await teardown_fixtures(pool)

        # ══════════════════════════════════════════════════════════════
        print("\n=== TASK 8: TEARDOWN — zero leftover rows and deployments ===\n")
        tokens = set_rls_context(None, True)
        try:
            async with pool.acquire() as conn2:
                leftover_orgs = await conn2.fetchval(
                    "SELECT count(*) FROM organizations WHERE id = ANY($1::uuid[])",
                    [FIXTURE_ORG_A_ID, FIXTURE_ORG_B_ID],
                )
                leftover_users = await conn2.fetchval(
                    "SELECT count(*) FROM users WHERE org_id = ANY($1::uuid[])",
                    [FIXTURE_ORG_A_ID, FIXTURE_ORG_B_ID],
                )
                leftover_settings = await conn2.fetchval(
                    "SELECT count(*) FROM org_settings WHERE org_id = ANY($1::uuid[])",
                    [FIXTURE_ORG_A_ID, FIXTURE_ORG_B_ID],
                )
                leftover_roles = await conn2.fetchval(
                    "SELECT count(*) FROM roles WHERE org_id = ANY($1::uuid[])",
                    [FIXTURE_ORG_A_ID, FIXTURE_ORG_B_ID],
                )
        finally:
            reset_rls_context(tokens)
        check("8. zero leftover fixture organizations rows", leftover_orgs == 0, f"count={leftover_orgs}")
        check("8. zero leftover fixture users rows", leftover_users == 0, f"count={leftover_users}")
        check("8. zero leftover fixture org_settings rows", leftover_settings == 0, f"count={leftover_settings}")
        check("8. zero leftover fixture roles rows", leftover_roles == 0, f"count={leftover_roles}")

        import time as _t
        _deadline = _t.monotonic() + 12.0
        final_deployments = {m.get("model_name") for m in _model_info()}
        while final_deployments != baseline_deployments and _t.monotonic() < _deadline:
            _t.sleep(1.0)
            final_deployments = {m.get("model_name") for m in _model_info()}
        check("8. proxy back to EXACTLY its pre-sprint deployment set "
              "(claude-sonnet, voyage-3.5 only — no leftover test deployments)",
              final_deployments == baseline_deployments,
              f"got {sorted(final_deployments)}")

        # Real orgs unaffected, re-confirmed after everything above.
        tokens = set_rls_context(None, True)
        try:
            for real_org, org_label in ((ORG, "2nd Act"), (HOLLIS, "Hollisworks")):
                for provider in ("anthropic", "voyage"):
                    key = lc.credential_source_key(provider)
                    async with pool.acquire() as c3:
                        value = await org_settings_mod.get_setting(c3, real_org, key)
                    check(f"8. {org_label}/{provider}: still resolves 'platform' "
                          f"after the whole run", value == "platform", f"got {value!r}")
        finally:
            reset_rls_context(tokens)

    finally:
        await pool.close()

    print(f"\n{'=' * 60}\n{_n_pass} PASS, {_n_fail} FAIL, {len(_finds)} FIND\n{'=' * 60}")
    return 0 if _ok else 1


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except Exception:
        traceback.print_exc()
        sys.exit(2)
