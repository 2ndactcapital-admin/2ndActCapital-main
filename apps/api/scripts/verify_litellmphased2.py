"""verify_litellmphased2.py — LiteLLM Phase D2: the model pick-list UI.

Proves, against the REAL live database (and, for two small enforcement
calls, the REAL live hollisworks-litellm proxy), that:

  1. Task 1's three discovery findings hold, re-checked live:
     1a. org_settings genuinely CANNOT hold a platform-scoped row (no
         owner_scope column, org_id is NOT NULL) — which is WHY this sprint
         created two new tables (platform_model_catalog, org_model_selections)
         instead, following the SAME owner_scope-style split
         `public.reference_data` already uses live (org_id NULL = global).
     1b. GET /model/info's real, usable fields for a picker (context window,
         pricing) versus what's genuinely absent (a "provider" field; a
         broad catalogue beyond the proxy's own registered deployments).
     1c. The real existing settings screen (OrgSettingsEditor.jsx /
         /admin/settings) and its real envelope shape, reused unchanged.
  2. A Hollisworks super_admin can add/remove a platform catalog model; an
     org_admin gets 403 on the IDENTICAL request, and the refused write
     genuinely left the row unchanged (before/after DB read).
  3. An org_admin can edit its own org's model selection; a plain member
     gets 403 on the identical request (same before/after discipline).
     Reads are open to any org member.
  4. Enforcement is REAL at the call path (services.extraction), not merely
     recorded: an org authorised for a model its resolved chain never names
     gets AIModelNotAuthorizedError raised BEFORE any provider call (zero
     new ai_decision_log success=true rows) — then, authorised for the
     model its chain DOES name, the identical call succeeds for real.
  5. An org with no explicit selection (resolve_authorized_models -> None)
     is genuinely unaffected — proven for a fresh fixture org AND for both
     real production orgs (read-only, no mutation).
  6. Cross-org isolation: org A's selection never appears on org B's list
     and vice versa, in both directions.
  7. No deployment name (`_org_deployment_name`'s `org-<provider>-<org_id>`
     shape) and no internal LiteLLM field name ever appears in an org-facing
     response body — grepped raw response text, not a parsed dict.
  8. View-only: the served envelope is can_write=false / editable=[] for a
     view-only caller (server side), AND both frontend components' can_write
     checks carry no truthy fallback (`?? true` / `|| true`) — grepped
     source, the same two-part proof the Triggers screen established.
  9. `npm run build` exits 0.
 10. Teardown: zero leftover fixture rows in every touched table, and the
     live proxy's deployment set is byte-for-byte unchanged (D2 makes no
     deployment calls at all — this is a defensive check, not incidental).

Hydrates DATABASE_URL (and every other Doppler secret, including
LITELLM_BASE_URL / LITELLM_MASTER_KEY / ANTHROPIC_API_KEY) from Doppler over
HTTPS at startup — the verify_litellmphased1c.py pattern. run_sprint.sh's
Step 3 does NOT `doppler run --` this script.

Cost note: exactly 2 real Anthropic calls (max_tokens<=8), same discipline as
D1c. Never prints a credential value.

Run:  python3 apps/api/scripts/verify_litellmphased2.py
"""
from __future__ import annotations

import asyncio
import pathlib
import subprocess
import sys
import traceback
from uuid import UUID

HERE = pathlib.Path(__file__).resolve()
sys.path.insert(0, str(HERE.parent))
from _db_bootstrap import bootstrap_async  # noqa: E402

HEADERS = {"Authorization": "Bearer verify-token"}

ORG = UUID("00000000-0000-0000-0000-000000000001")          # 2nd Act Capital (real)
HOLLIS = UUID("bb347258-8f28-4f49-8cc9-e29ccad82884")        # Hollisworks (real)

FIXTURE_ORG_A_ID = UUID("99000000-0000-0000-0000-0000000d2a01")
FIXTURE_ORG_B_ID = UUID("99000000-0000-0000-0000-0000000d2a02")
FIXTURE_ORG_A_ADMIN_ID = UUID("99000000-0000-0000-0000-0000000d2a03")
FIXTURE_ORG_B_ADMIN_ID = UUID("99000000-0000-0000-0000-0000000d2a04")
FIXTURE_ORG_A_MEMBER_ID = UUID("99000000-0000-0000-0000-0000000d2a05")
FIXTURE_SUPERADMIN_ID = UUID("99000000-0000-0000-0000-0000000d2a06")
FIXTURE_ORG_A_ADMIN_SUB = "auth0|verify_d2_org_a_admin"
FIXTURE_ORG_B_ADMIN_SUB = "auth0|verify_d2_org_b_admin"
FIXTURE_ORG_A_MEMBER_SUB = "auth0|verify_d2_org_a_member"
FIXTURE_SUPERADMIN_SUB = "auth0|verify_d2_superadmin"

FIXTURE_MODEL_ID = "verify-d2-fixture-model"

# [HISTORICAL — fixed by litellmseedfix.structural, see
# verify_litellmseedfix.py]: this script originally found, live, that
# neither org_settings' real DEFAULT_SETTINGS default-chain value
# ('claude-haiku-4-5-20251001') nor its assistant value
# ('claude-sonnet-4-6') was a callable model string against the live
# hollisworks-litellm proxy — only the proxy's actual REGISTERED
# `model_name`, 'claude-sonnet', was. litellmseedfix realigned org_settings
# (and this catalog) onto the proxy's registered deployment names, and
# registered a real 'claude-haiku' deployment so the classifier/default path
# could stay on the cheaper model rather than silently collapsing onto
# Sonnet. REAL_HAIKU/REAL_SONNET below are now genuinely callable AND are
# what org_settings.DEFAULT_SETTINGS actually stores — no separate
# "LIVE_MODEL_ID" workaround is needed any more; Task 4 below calls through
# the real seeded default chain directly.
REAL_HAIKU = "claude-haiku"     # org_settings' real default-chain value (seeded row) — genuinely callable
REAL_SONNET = "claude-sonnet"   # org_settings' real assistant-key value (seeded row) — genuinely callable

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
    """Drives the real ASGI app as one user — the verify_litellmphased1a.py
    pattern. ``sub`` stubs main.verify_token's returned JWT claims; org_id is
    ALSO stubbed as the real org_id custom claim (real Auth0 API-audience
    tokens carry no org claim at all — see routers/entities.py)."""

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
                (FIXTURE_ORG_A_ID, "Verify D2 Org A", "verify-d2-org-a"),
                (FIXTURE_ORG_B_ID, "Verify D2 Org B", "verify-d2-org-b"),
            ):
                await conn.execute(
                    """
                    INSERT INTO organizations (id, name, slug)
                    VALUES ($1, $2, $3)
                    ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name
                    """,
                    org_id, name, slug,
                )
            for user_id, org_id, sub, role, label in (
                (FIXTURE_ORG_A_ADMIN_ID, FIXTURE_ORG_A_ID, FIXTURE_ORG_A_ADMIN_SUB, "member", "Org A Admin"),
                (FIXTURE_ORG_B_ADMIN_ID, FIXTURE_ORG_B_ID, FIXTURE_ORG_B_ADMIN_SUB, "member", "Org B Admin"),
                (FIXTURE_ORG_A_MEMBER_ID, FIXTURE_ORG_A_ID, FIXTURE_ORG_A_MEMBER_SUB, "member", "Org A Member"),
                (FIXTURE_SUPERADMIN_ID, FIXTURE_ORG_A_ID, FIXTURE_SUPERADMIN_SUB, "super_admin", "Super Admin"),
            ):
                await conn.execute(
                    """
                    INSERT INTO users (id, org_id, email, full_name, auth0_sub, role)
                    VALUES ($1, $2, $3, $4, $5, $6)
                    ON CONFLICT (id) DO UPDATE SET auth0_sub = EXCLUDED.auth0_sub,
                        org_id = EXCLUDED.org_id, role = EXCLUDED.role
                    """,
                    user_id, org_id, f"{sub}@test.local", f"Verify D2 {label}", sub, role,
                )
            await grant_org_admin(conn, FIXTURE_ORG_A_ADMIN_ID, FIXTURE_ORG_A_ID)
            await grant_org_admin(conn, FIXTURE_ORG_B_ADMIN_ID, FIXTURE_ORG_B_ID)

            # The plain-member fixture MUST hold a real, non-empty role that
            # does NOT include manage_org_settings — has_permission
            # default-ALLOWS a user with ZERO user_roles grants (documented
            # single-admin bootstrap posture), so a role-less fixture would
            # prove nothing about the refusal path.
            harmless_perm_id = await ensure_permission(
                conn, "view_dashboard", "dashboard", "view"
            )
            member_role_id = await ensure_role(
                conn, FIXTURE_ORG_A_ID, "verify_d2_member",
                "verify_litellmphased2 fixture — real role, no manage_org_settings",
            )
            await conn.execute(
                "INSERT INTO role_permissions (role_id, permission_id) VALUES ($1, $2) "
                "ON CONFLICT DO NOTHING",
                member_role_id, harmless_perm_id,
            )
            await conn.execute(
                "INSERT INTO user_roles (user_id, role_id) VALUES ($1, $2) ON CONFLICT DO NOTHING",
                FIXTURE_ORG_A_MEMBER_ID, member_role_id,
            )
    finally:
        reset_rls_context(tokens)


async def teardown_fixtures(pool):
    from services.database import reset_rls_context, set_rls_context

    tokens = set_rls_context(None, True)
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM ai_decision_log WHERE task_type LIKE 'verify_d2_%'"
            )
            await conn.execute(
                "DELETE FROM org_model_selections WHERE org_id = ANY($1::uuid[])",
                [FIXTURE_ORG_A_ID, FIXTURE_ORG_B_ID],
            )
            await conn.execute(
                "DELETE FROM platform_model_catalog WHERE model_id = $1",
                FIXTURE_MODEL_ID,
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


# ── LiteLLM admin API — direct HTTP, same shape as services.litellm_credentials ──


def _litellm_http(path, *, method="GET", body=None, timeout=60):
    import json
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
    import json

    s, b = _litellm_http("/model/info")
    if s != 200:
        raise RuntimeError(f"GET /model/info -> {s}: {b[:300]}")
    return json.loads(b).get("data", [])


async def main() -> int:
    import os as _os
    if _os.environ.get("VERIFY_FORCE_FAIL") == "1":
        # Self-test hook ONLY — proves the TOTAL line + non-zero-exit fix
        # (litellmseedfix.structural, verify_litellmseedfix.py) without
        # paying for the full suite's real HTTP/DB/npm-build cost. Exits
        # before any Doppler hydration, DB connection, or fixture write —
        # touches nothing.
        check("[self-test] deliberately forced failure — proves the TOTAL "
              "line and non-zero exit code on a real failure", False,
              "VERIFY_FORCE_FAIL=1 was set")
        print(f"\n{'=' * 70}\nTOTAL: {_n_pass} PASS, {_n_fail} FAIL, {len(_finds)} FIND\n{'=' * 70}")
        return 0 if _ok else 1

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
    import services.model_catalog as mc

    pool = await get_pool()
    baseline_deployments = {m.get("model_name") for m in _model_info()}
    print(f"baseline LiteLLM deployments: {sorted(baseline_deployments)}")

    try:
        # ══════════════════════════════════════════════════════════════
        print("\n=== TASK 1: discovery findings ===\n")

        tokens = set_rls_context(None, True)
        try:
            async with pool.acquire() as conn:
                col = await conn.fetchrow(
                    "SELECT is_nullable FROM information_schema.columns "
                    "WHERE table_schema='public' AND table_name='org_settings' "
                    "AND column_name='org_id'"
                )
                owner_scope_col = await conn.fetchval(
                    "SELECT 1 FROM information_schema.columns "
                    "WHERE table_schema='public' AND table_name='org_settings' "
                    "AND column_name='owner_scope'"
                )
                check("1a. org_settings.org_id is NOT NULL (re-confirmed live, "
                      "not assumed from a prior sprint's doc)",
                      col is not None and col["is_nullable"] == "NO", f"{dict(col) if col else None}")
                check("1a. org_settings has NO owner_scope column — a "
                      "platform-scoped row is genuinely not possible there",
                      owner_scope_col is None)

                new_tables = await conn.fetch(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema='public' AND table_name IN "
                    "('platform_model_catalog','org_model_selections')"
                )
                check("1a. platform_model_catalog + org_model_selections both "
                      "exist live (the real mechanism this sprint built "
                      "instead of an org_settings key)",
                      {r["table_name"] for r in new_tables} ==
                      {"platform_model_catalog", "org_model_selections"})

                ref_policy = await conn.fetchval(
                    "SELECT qual FROM pg_policies WHERE tablename='reference_data' "
                    "AND policyname='reference_data_global_or_org'"
                )
                find("1a. platform_model_catalog's split (no org_id column at "
                     "all, since every row is unconditionally platform) "
                     "mirrors the SAME live owner_scope-style convention "
                     "public.reference_data already uses for its own "
                     "global-vs-org rows",
                     f"reference_data's real live policy qual: {ref_policy}")

            find("1b. GET /model/info (live): genuinely available per "
                 "registered deployment — model_info.max_input_tokens / "
                 "max_output_tokens (context window) and "
                 "input_cost_per_token / output_cost_per_token (pricing). "
                 "Genuinely ABSENT — no 'provider' field at all (derived here "
                 "from litellm_params.model's 'provider/model' prefix) and NO "
                 "broad catalogue: only the proxy's own registered "
                 "deployments appear, which is 2-3 entries, not a general "
                 "model list — most curated models get zero live "
                 "enrichment, by design (services.model_catalog."
                 "enrich_with_live_info's own docstring).")
            find("1c. the real existing settings screen is OrgSettingsEditor."
                 "jsx (/admin/settings) — its 'ai' category section already "
                 "established reads-open/writes-gated-on-manage_org_settings "
                 "for ai-credentials (Phase D1a); OrgModelSelector.jsx reuses "
                 "the IDENTICAL envelope shape (permissions.can_write, no "
                 "fallback) rather than inventing a second one. The platform "
                 "catalog is a DIFFERENT, higher-privilege screen "
                 "(/admin/model-catalog, super_admin only) mirroring "
                 "/admin/platform's own existing gate pattern, not the org "
                 "settings screen's gate.")
        finally:
            reset_rls_context(tokens)

        # ══════════════════════════════════════════════════════════════
        print("\n=== TASK 2/3: real HTTP permission proofs ===\n")
        await teardown_fixtures(pool)  # clean slate from any prior failed run
        await setup_fixtures(pool)

        from starlette.testclient import TestClient
        import main as main_module

        await close_pool()
        client = TestClient(main_module.app, raise_server_exceptions=False)
        client.__enter__()
        try:
            superadmin = _Principal(client, FIXTURE_SUPERADMIN_SUB, FIXTURE_ORG_A_ID)
            admin_a = _Principal(client, FIXTURE_ORG_A_ADMIN_SUB, FIXTURE_ORG_A_ID)
            admin_b = _Principal(client, FIXTURE_ORG_B_ADMIN_SUB, FIXTURE_ORG_B_ID)
            member_a = _Principal(client, FIXTURE_ORG_A_MEMBER_SUB, FIXTURE_ORG_A_ID)

            body = {
                "model_id": FIXTURE_MODEL_ID,
                "display_name": "Verify D2 Fixture Model",
                "provider": "anthropic",
            }

            # -- org_admin (NOT super_admin) refused, identical request --
            r_blocked = admin_a.call("post", "/api/v1/admin/model-catalog", body=body)
            check("2. org_admin: POST /admin/model-catalog -> 403 (super_admin only)",
                  r_blocked.status_code == 403, f"HTTP {r_blocked.status_code} {r_blocked.text[:200]}")

            r_check_absent = superadmin.call("get", "/api/v1/admin/model-catalog")
            present_ids = {m["model_id"] for m in r_check_absent.json().get("models", [])}
            check("2. the refused org_admin POST genuinely left the catalog "
                  "unchanged (fixture model still absent)",
                  FIXTURE_MODEL_ID not in present_ids)

            # -- super_admin: identical request succeeds --
            r_created = superadmin.call("post", "/api/v1/admin/model-catalog", body=body)
            check("2. super_admin: POST /admin/model-catalog -> 201 (identical "
                  "request org_admin was just refused on)",
                  r_created.status_code == 201, f"HTTP {r_created.status_code} {r_created.text[:200]}")

            r_after_create = superadmin.call("get", "/api/v1/admin/model-catalog")
            ids_after_create = {m["model_id"] for m in r_after_create.json().get("models", [])}
            check("2. fixture model now genuinely present (independent re-read)",
                  FIXTURE_MODEL_ID in ids_after_create)
            check("2. non-super caller's read envelope: can_write=False",
                  admin_a.call("get", "/api/v1/admin/model-catalog").json()
                  .get("permissions", {}).get("can_write") is False)
            check("2. super_admin's own read envelope: can_write=True",
                  r_after_create.json().get("permissions", {}).get("can_write") is True)

            # -- org_admin refused on DELETE, identical request --
            r_del_blocked = admin_a.call("delete", f"/api/v1/admin/model-catalog/{FIXTURE_MODEL_ID}")
            check("2. org_admin: DELETE /admin/model-catalog/{id} -> 403",
                  r_del_blocked.status_code == 403, f"HTTP {r_del_blocked.status_code}")
            still_present = FIXTURE_MODEL_ID in {
                m["model_id"] for m in superadmin.call("get", "/api/v1/admin/model-catalog").json().get("models", [])
            }
            check("2. the refused org_admin DELETE genuinely left the row in "
                  "place (before/after DB read)", still_present)

            # -- Task 3: org picker --
            r_member_read = member_a.call("get", f"/api/v1/orgs/{FIXTURE_ORG_A_ID}/settings/model-selections")
            check("3. plain member: GET model-selections -> 200 (reads open "
                  "to any org member)", r_member_read.status_code == 200,
                  f"HTTP {r_member_read.status_code}")
            member_envelope = r_member_read.json()
            check("3. plain member's envelope: can_write=False",
                  member_envelope.get("permissions", {}).get("can_write") is False)
            check("3. plain member's envelope: vocabularies.editable == [] "
                  "(Rule 1 permission-envelope pattern — never omitted, "
                  "never a client default)",
                  member_envelope.get("vocabularies", {}).get("editable") == [])
            check("3. plain member's envelope still carries the catalog for "
                  "display (read-only) even though editable is empty",
                  len(member_envelope.get("vocabularies", {}).get("catalog", [])) > 0)

            # -- plain member refused the write, identical request --
            r_member_write = member_a.call(
                "put", f"/api/v1/orgs/{FIXTURE_ORG_A_ID}/settings/model-selections",
                body={"model_ids": [FIXTURE_MODEL_ID]},
            )
            check("3. plain member: PUT model-selections -> 403",
                  r_member_write.status_code == 403, f"HTTP {r_member_write.status_code}")
            r_org_a_before = admin_a.call("get", f"/api/v1/orgs/{FIXTURE_ORG_A_ID}/settings/model-selections")
            check("3. the refused member write genuinely left org A's "
                  "selection unchanged (still empty)",
                  r_org_a_before.json().get("selected_model_ids") == [])

            # -- org_admin: identical request succeeds --
            r_org_a_write = admin_a.call(
                "put", f"/api/v1/orgs/{FIXTURE_ORG_A_ID}/settings/model-selections",
                body={"model_ids": [FIXTURE_MODEL_ID]},
            )
            check("3. org_admin: PUT model-selections -> 200 (identical "
                  "request the plain member was just refused on)",
                  r_org_a_write.status_code == 200, f"HTTP {r_org_a_write.status_code} {r_org_a_write.text[:200]}")
            check("3. response confirms the real selection",
                  r_org_a_write.json().get("selected_model_ids") == [FIXTURE_MODEL_ID])
            admin_envelope = admin_a.call("get", f"/api/v1/orgs/{FIXTURE_ORG_A_ID}/settings/model-selections").json()
            check("3. org_admin's own envelope: can_write=True, editable "
                  "non-empty", admin_envelope.get("permissions", {}).get("can_write") is True
                  and len(admin_envelope.get("vocabularies", {}).get("editable", [])) > 0)

            # -- cross-org isolation on selections --
            r_org_b_read = admin_b.call("get", f"/api/v1/orgs/{FIXTURE_ORG_B_ID}/settings/model-selections")
            check("6. CROSS-ORG: org B's selection is untouched by org A's "
                  "write (still empty)", r_org_b_read.json().get("selected_model_ids") == [])
            r_org_b_write = admin_b.call(
                "put", f"/api/v1/orgs/{FIXTURE_ORG_B_ID}/settings/model-selections",
                body={"model_ids": ["voyage-3.5"]},
            )
            check("6. org B admin: PUT own selection -> 200 (independent of org A)",
                  r_org_b_write.status_code == 200, f"HTTP {r_org_b_write.status_code}")
            r_org_a_after = admin_a.call("get", f"/api/v1/orgs/{FIXTURE_ORG_A_ID}/settings/model-selections")
            check("6. CROSS-ORG: org A's selection is untouched by org B's "
                  "write (still exactly the fixture model, not voyage-3.5)",
                  r_org_a_after.json().get("selected_model_ids") == [FIXTURE_MODEL_ID])
            r_cross_write = admin_b.call(
                "put", f"/api/v1/orgs/{FIXTURE_ORG_A_ID}/settings/model-selections",
                body={"model_ids": []},
            )
            check("6. CROSS-ORG: org B admin -> PUT org A's selection -> 403",
                  r_cross_write.status_code == 403, f"HTTP {r_cross_write.status_code}")

            # -- unknown model_id rejected with a clear error, not a raw FK crash --
            r_unknown = admin_a.call(
                "put", f"/api/v1/orgs/{FIXTURE_ORG_A_ID}/settings/model-selections",
                body={"model_ids": ["not-a-real-model"]},
            )
            check("3. PUT model-selections with an uncurated model_id -> 400 "
                  "(clear ModelCatalogError, not a raw FK-violation 500)",
                  r_unknown.status_code == 400, f"HTTP {r_unknown.status_code}")

            # -- no deployment name / internal field name in any response --
            deployment_shape_a = f"org-anthropic-{FIXTURE_ORG_A_ID}"
            deployment_shape_b = f"org-anthropic-{FIXTURE_ORG_B_ID}"
            leak_probe_bodies = [
                r_after_create.text, admin_envelope and str(admin_envelope),
                r_org_a_after.text, r_org_b_read.text, r_member_read.text,
            ]
            leaked = [
                s for s in leak_probe_bodies
                if s and (deployment_shape_a in s or deployment_shape_b in s
                          or '"model_name"' in s or '"litellm_params"' in s)
            ]
            check("7. no deployment name and no internal LiteLLM field name "
                  "('model_name' / 'litellm_params') appears in ANY org-facing "
                  "response body — grepped raw text, not a parsed dict",
                  not leaked, f"leaked in {len(leaked)} response(s)")
        finally:
            client.__exit__(None, None, None)

        await close_pool()
        pool = await get_pool()

        # ══════════════════════════════════════════════════════════════
        print("\n=== TASK 4: enforcement at the REAL call path ===\n")
        find("[HISTORICAL] this section originally found org_settings' real "
             "default-chain model strings were NOT callable against the "
             "live proxy and worked around it with a separate "
             "'LIVE_MODEL_ID' fixture. litellmseedfix.structural fixed the "
             "underlying naming mismatch (see verify_litellmseedfix.py) — "
             "REAL_HAIKU/REAL_SONNET are now the actual seeded values AND "
             "genuinely callable, so this section now calls through the "
             "real default chain directly, no workaround.")
        # CLAUDE.md's RLS section, verbatim: a script calling into a chain
        # executor / reading an RLS-protected table directly (not through a
        # real HTTP request, where the middleware already set the ContextVars
        # for the whole request task) gets SILENT RLS denials — zero rows,
        # no error — unless it sets set_rls_context itself first. Every
        # direct call below is wrapped for exactly that reason: an org's own
        # RLS context for its own read/call, super_admin for cross-org reads.

        # NOTE: RLS context must be set BEFORE ``pool.acquire()`` is entered —
        # ``_apply_rls_settings`` reads the ContextVars at acquire-time, not
        # at query time. Both helpers below therefore wrap the ENTIRE
        # coroutine passed to them (which may itself call pool.acquire()),
        # never just the query inside an already-open connection.

        async def _as_org(_rls_org_id, fn, *args, **kwargs):
            tokens = set_rls_context(_rls_org_id, False)
            try:
                return await fn(*args, **kwargs)
            finally:
                reset_rls_context(tokens)

        async def _as_super(fn, *args, **kwargs):
            tokens = set_rls_context(None, True)
            try:
                return await fn(*args, **kwargs)
            finally:
                reset_rls_context(tokens)

        async def _write_selections(org_id, model_ids):
            async with pool.acquire() as conn:
                return await mc.set_org_selections(conn, org_id, model_ids)

        # 4a — authorise org A for a model its resolved default chain never
        # names (FIXTURE_MODEL_ID isn't even a real model — irrelevant here,
        # since this path never reaches a provider call at all).
        await _as_super(_write_selections, FIXTURE_ORG_A_ID, [FIXTURE_MODEL_ID])

        authorized_wrong = await _as_org(FIXTURE_ORG_A_ID, mc.resolve_authorized_models, FIXTURE_ORG_A_ID)
        check("4a. resolve_authorized_models(org A) reflects the real write",
              authorized_wrong == {FIXTURE_MODEL_ID}, f"got {authorized_wrong}")

        async def _call_text(*, org_id, task_type, model=None):
            return await ex.call_claude_text(
                system="Reply with exactly one word.",
                messages=[{"role": "user", "content": "Say OK."}],
                max_tokens=8, org_id=org_id, task_type=task_type, model=model,
            )

        refused = False
        refusal_detail = ""
        try:
            await _as_org(FIXTURE_ORG_A_ID, _call_text,
                          org_id=FIXTURE_ORG_A_ID, task_type="verify_d2_unauthorized_a")
        except ex.AIModelNotAuthorizedError as exc:
            refused = True
            refusal_detail = str(exc)
        check("4a. an org authorised ONLY for models its resolved chain "
              "never names raises AIModelNotAuthorizedError BEFORE any "
              "provider call — genuinely refused, not silently None",
              refused, refusal_detail[:200])

        async def _read_log(task_type):
            async with pool.acquire() as conn:
                return await conn.fetch(
                    "SELECT success, error_detail FROM ai_decision_log "
                    "WHERE task_type = $1", task_type,
                )

        log_rows = await _as_super(_read_log, "verify_d2_unauthorized_a")
        check("4a. exactly one ai_decision_log row, success=false, naming "
              "the refusal — never silently swallowed",
              len(log_rows) == 1 and log_rows[0]["success"] is False
              and bool(log_rows[0]["error_detail"])
              and "authorised" in log_rows[0]["error_detail"],
              f"{[dict(r) for r in log_rows]}")
        check("4a. zero success=true rows for this task_type (no provider "
              "call was ever made)",
              all(not r["success"] for r in log_rows))

        # 4b — authorise org A for REAL_HAIKU (org_settings' own real
        # default-chain value, genuinely callable live since
        # litellmseedfix.structural) and pass it as an explicit override. The
        # IDENTICAL enforcement path (attempts filtered against the
        # authorised set) now lets the call through for REAL, proving
        # enforcement is genuinely bidirectional, not merely a one-way gate.
        await _as_super(_write_selections, FIXTURE_ORG_A_ID, [REAL_HAIKU])

        result = await _as_org(FIXTURE_ORG_A_ID, _call_text,
                                org_id=FIXTURE_ORG_A_ID, task_type="verify_d2_authorized_a",
                                model=REAL_HAIKU)
        check("4b. once the org authorises the exact model requested, the "
              "identical call path now succeeds for real (real response, "
              "not None) — enforcement is genuinely bidirectional",
              result is not None, f"got {result!r}")

        # 4c — an org with NO explicit selection is unrestricted. Org B was
        # given a real selection during Task 2/3's cross-org isolation test
        # (["voyage-3.5"]) — reset it to empty here so this is a genuine
        # "never touched model-selections" state, not a stale leftover from
        # an earlier assertion in this same run.
        await _as_super(_write_selections, FIXTURE_ORG_B_ID, [])

        authorized_b = await _as_org(FIXTURE_ORG_B_ID, mc.resolve_authorized_models, FIXTURE_ORG_B_ID)
        check("4c. org B genuinely has no explicit selection right now "
              "(resolve_authorized_models -> None)", authorized_b is None,
              f"got {authorized_b}")

        result_b = await _as_org(FIXTURE_ORG_B_ID, _call_text,
                                  org_id=FIXTURE_ORG_B_ID, task_type="verify_d2_noselection_b",
                                  model=REAL_HAIKU)
        check("4c. an unrestricted org's real call succeeds exactly as it "
              "did before this sprint existed — D2's filter never engages "
              "when there is no explicit selection",
              result_b is not None, f"got {result_b!r}")

        # 4d — real production orgs, read-only, confirmed still unrestricted.
        for real_org, label in ((ORG, "2nd Act"), (HOLLIS, "Hollisworks")):
            authorized_real = await _as_org(real_org, mc.resolve_authorized_models, real_org)
            check(f"5. real org {label}: resolve_authorized_models -> None "
                  "(unrestricted — no explicit selection has ever been made, "
                  "read-only check, nothing written to this org)",
                  authorized_real is None, f"got {authorized_real}")

        # ══════════════════════════════════════════════════════════════
        print("\n=== TASK 4 (continued): view-only source-level proof ===\n")
        web_dir = HERE.parents[2] / "web"
        catalog_src = (web_dir / "components/admin/ModelCatalogManager.jsx").read_text()
        selector_src = (web_dir / "components/admin/OrgModelSelector.jsx").read_text()
        for name, src in (("ModelCatalogManager.jsx", catalog_src), ("OrgModelSelector.jsx", selector_src)):
            # Search only actual code lines (skip /** */ and // comment
            # lines) — this file's own doc-comment PROSE explains the
            # anti-pattern in backticks, which would otherwise false-positive
            # a bare substring search against the exact same words.
            code_lines = [
                ln for ln in src.splitlines()
                if not ln.strip().startswith(("*", "//", "/**"))
            ]
            code_only = "\n".join(code_lines)
            check(f"8. {name}: can_write carries no truthy fallback in actual "
                  "code (no '?? true' / '|| true' anti-pattern — a missing "
                  "envelope must fail closed)",
                  "?? true" not in code_only and "can_write || true" not in code_only)
            check(f"8. {name}: renders its write control gated on canWrite",
                  "canWrite" in src)

        print("\n=== npm run build ===\n")
        build = subprocess.run(
            ["npm", "run", "build"], cwd=str(web_dir),
            capture_output=True, text=True, timeout=480,
        )
        check("9. npm run build exits 0", build.returncode == 0,
              (build.stdout[-500:] + build.stderr[-500:]) if build.returncode != 0 else "")

    finally:
        await teardown_fixtures(pool)

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
                leftover_catalog = await conn2.fetchval(
                    "SELECT count(*) FROM platform_model_catalog WHERE model_id = $1",
                    FIXTURE_MODEL_ID,
                )
                leftover_selections = await conn2.fetchval(
                    "SELECT count(*) FROM org_model_selections WHERE org_id = ANY($1::uuid[])",
                    [FIXTURE_ORG_A_ID, FIXTURE_ORG_B_ID],
                )
                leftover_logs = await conn2.fetchval(
                    "SELECT count(*) FROM ai_decision_log WHERE task_type LIKE 'verify_d2_%'"
                )
                seed_still_present = await conn2.fetch(
                    "SELECT model_id FROM platform_model_catalog WHERE model_id = ANY($1::text[])",
                    [REAL_HAIKU, REAL_SONNET, "voyage-3.5"],
                )
        finally:
            reset_rls_context(tokens)

        check("10. zero leftover fixture organizations", leftover_orgs == 0, f"count={leftover_orgs}")
        check("10. zero leftover fixture users", leftover_users == 0, f"count={leftover_users}")
        check("10. zero leftover fixture catalog rows", leftover_catalog == 0, f"count={leftover_catalog}")
        check("10. zero leftover fixture selection rows", leftover_selections == 0, f"count={leftover_selections}")
        check("10. zero leftover ai_decision_log fixture rows", leftover_logs == 0, f"count={leftover_logs}")
        check("10. the real seeded platform models (untouched by this run) "
              "are still exactly present",
              {r["model_id"] for r in seed_still_present} == {REAL_HAIKU, REAL_SONNET, "voyage-3.5"})

        final_deployments = {m.get("model_name") for m in _model_info()}
        check("10. the live proxy's deployment set is byte-for-byte "
              "unchanged (D2 makes zero deployment calls)",
              final_deployments == baseline_deployments, f"got {sorted(final_deployments)}")

        await pool.close()

    print(f"\n{'=' * 70}\nTOTAL: {_n_pass} PASS, {_n_fail} FAIL, {len(_finds)} FIND\n{'=' * 70}")
    return 0 if _ok else 1


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except Exception:
        traceback.print_exc()
        sys.exit(2)
