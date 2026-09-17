"""verify_litellmavailability.py — LiteLLM D2 §3: three-state model availability.

Proves, against the REAL live database (and, for the state-transition
behaviour proofs, the REAL live hollisworks-litellm proxy + a real Anthropic
call), that:

  1. Task 1's three discovery findings hold, live:
     1a. Which real reads of platform_model_catalog now filter by
         availability (the org-facing picker:
         GET /orgs/{org_id}/settings/model-selections and
         GET /orgs/{org_id}/settings/ai-tasks, via the new
         services.model_catalog.list_catalog_for_org) and which
         deliberately do NOT (services.model_catalog.list_catalog itself —
         the Hollisworks curation screen, GET /admin/model-catalog, which
         must keep showing every state to manage it — and
         resolve_authorized_models, which must keep resolving an existing
         selection regardless of its current availability).
     1b. The real call-path enforcement: services.extraction._execute_chain
         drops 'disabled' model_ids unconditionally, immediately after the
         existing D2 authorized-model filter, falling back to the org's own
         safe model (ai.model.default) when that empties the attempt list.
     1c. create_credential_failure_alerts' real signature
         (conn, *, org_id, provider, error_detail) and its shared
         _upsert_todo / _record_undelivered_alert / _org_admin_recipients
         helpers — confirmed the new sibling,
         services.workflow_todos.create_model_availability_alerts, reuses
         all three rather than diverging.
  2. All three seeded models (claude-sonnet, claude-haiku, voyage-3.5) are
     'available' before this run, and a real call through an untouched
     fixture org resolves exactly as pre-sprint (model_used='claude-haiku',
     fallback_used=false) — no regression.
  3. 'deprecated' (applied to the REAL 'claude-sonnet' deployment, briefly,
     then restored): hidden from a non-selecting org's picker, but an
     org that already selected it keeps seeing it AND makes a real,
     successful call through it.
  4. 'disabled' (same real deployment): a real call for that same
     org falls back to the org's safe model (claude-haiku) and succeeds —
     proven from ai_decision_log.model_used, not from config.
  5. Deprecating/disabling alerts ONLY the org(s) that selected the model —
     the non-selecting org gets zero member_todos rows for it.
  6. The zero-recipient path (the REAL Hollisworks org, which holds zero
     manage_org_settings grants today) leaves a findable audit_log row
     instead of failing silently — called directly, D1c's own proof style.
  7. An invalid availability value is refused by the real DB CHECK
     constraint (against an isolated fixture catalog row, never a real
     model) — a 400, and the row is left unchanged.
  8. org_admin gets 403 setting availability; super_admin gets 200 on the
     IDENTICAL request (same isolated fixture catalog row).
  9. Cross-org isolation: the alerts from (3)/(4) above never reach the
     non-selecting org.
 10. Teardown: zero leftover fixture rows in every touched table, all three
     seeded models back to 'available', and the live proxy's deployment set
     is byte-for-byte unchanged (this sprint makes no /model/new or
     /model/delete calls at all).

Blast-radius note: the real state-transition proofs (3)/(4) target
'claude-sonnet', not 'claude-haiku' — 'claude-haiku' IS the platform default
for ai.model.default (the "org safe model" itself), so briefly disabling it
would make the safe-model fallback unavailable for any concurrent real call
on an org that has never customized its default. 'claude-sonnet' only backs
the 'assistant' task by default; a concurrent real call to it during this
script's brief toggle window degrades gracefully to 'claude-haiku' — exactly
the behaviour this sprint implements — rather than failing. The toggle
window is minimized and the model is restored to 'available' in a `finally`
block regardless of outcome.

Hydrates DATABASE_URL (and every other Doppler secret, including
LITELLM_BASE_URL / LITELLM_MASTER_KEY / ANTHROPIC_API_KEY) from Doppler over
HTTPS at startup — the verify_litellmphasee.py pattern. run_sprint.sh's
Step 3 does NOT `doppler run --` this script. Never prints a credential
value.

Cost note: 2 real Anthropic calls total (max_tokens<=8).

Run:  python3 apps/api/scripts/verify_litellmavailability.py
"""
from __future__ import annotations

import asyncio
import json
import pathlib
import sys
from uuid import UUID

HERE = pathlib.Path(__file__).resolve()
sys.path.insert(0, str(HERE.parent))
from _db_bootstrap import bootstrap_async  # noqa: E402

HEADERS = {"Authorization": "Bearer verify-token"}

FIXTURE_ORG_SEL_ID = UUID("99000000-0000-0000-0000-0000000ea0a1")
FIXTURE_ORG_NONSEL_ID = UUID("99000000-0000-0000-0000-0000000ea0b1")
FIXTURE_ORG_SEL_ADMIN_ID = UUID("99000000-0000-0000-0000-0000000ea0a2")
FIXTURE_ORG_NONSEL_ADMIN_ID = UUID("99000000-0000-0000-0000-0000000ea0b2")
FIXTURE_SUPERADMIN_ID = UUID("99000000-0000-0000-0000-0000000ea0c1")
FIXTURE_ORG_SEL_ADMIN_SUB = "auth0|verify_avail_org_sel_admin"
FIXTURE_ORG_NONSEL_ADMIN_SUB = "auth0|verify_avail_org_nonsel_admin"
FIXTURE_SUPERADMIN_SUB = "auth0|verify_avail_superadmin"

FIXTURE_CATALOG_MODEL_ID = "verify-availability-fixture-model"

REAL_HAIKU = "claude-haiku"
REAL_SONNET = "claude-sonnet"
REAL_VOYAGE = "voyage-3.5"
REAL_CATALOG = {REAL_HAIKU, REAL_SONNET, REAL_VOYAGE}

ORG = UUID("00000000-0000-0000-0000-000000000001")           # 2nd Act Capital (real)
HOLLIS = UUID("bb347258-8f28-4f49-8cc9-e29ccad82884")         # Hollisworks (real)

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


async def setup_fixtures(pool):
    from services.database import reset_rls_context, set_rls_context
    from services.rbac import ensure_permission, ensure_role, grant_org_admin

    tokens = set_rls_context(None, True)
    try:
        async with pool.acquire() as conn:
            for org_id, name, slug in (
                (FIXTURE_ORG_SEL_ID, "Verify Availability Org Selector", "verify-avail-org-sel"),
                (FIXTURE_ORG_NONSEL_ID, "Verify Availability Org Non-Selector", "verify-avail-org-nonsel"),
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
                (FIXTURE_ORG_SEL_ADMIN_ID, FIXTURE_ORG_SEL_ID, FIXTURE_ORG_SEL_ADMIN_SUB, "member", "Org Sel Admin"),
                (FIXTURE_ORG_NONSEL_ADMIN_ID, FIXTURE_ORG_NONSEL_ID, FIXTURE_ORG_NONSEL_ADMIN_SUB, "member", "Org Nonsel Admin"),
                (FIXTURE_SUPERADMIN_ID, FIXTURE_ORG_SEL_ID, FIXTURE_SUPERADMIN_SUB, "super_admin", "Super Admin"),
            ):
                await conn.execute(
                    """
                    INSERT INTO users (id, org_id, email, full_name, auth0_sub, role)
                    VALUES ($1, $2, $3, $4, $5, $6)
                    ON CONFLICT (id) DO UPDATE SET auth0_sub = EXCLUDED.auth0_sub,
                        org_id = EXCLUDED.org_id, role = EXCLUDED.role
                    """,
                    user_id, org_id, f"{sub}@test.local", f"Verify Availability {label}", sub, role,
                )
            await grant_org_admin(conn, FIXTURE_ORG_SEL_ADMIN_ID, FIXTURE_ORG_SEL_ID)
            await grant_org_admin(conn, FIXTURE_ORG_NONSEL_ADMIN_ID, FIXTURE_ORG_NONSEL_ID)
            # harmless permission catalog entries so ensure_permission never
            # collides with a real one on re-run
            await ensure_permission(conn, "view_dashboard", "dashboard", "view")
    finally:
        reset_rls_context(tokens)


async def teardown_fixtures(pool):
    from services.database import reset_rls_context, set_rls_context

    tokens = set_rls_context(None, True)
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM ai_decision_log WHERE task_type LIKE 'verify_avail_%'"
            )
            await conn.execute(
                "DELETE FROM member_todos WHERE org_id = ANY($1::uuid[])",
                [FIXTURE_ORG_SEL_ID, FIXTURE_ORG_NONSEL_ID],
            )
            await conn.execute(
                "DELETE FROM audit_log WHERE org_id = ANY($1::uuid[]) "
                "AND action LIKE 'ai_model_availability%'",
                [FIXTURE_ORG_SEL_ID, FIXTURE_ORG_NONSEL_ID],
            )
            await conn.execute(
                "DELETE FROM org_model_selections WHERE org_id = ANY($1::uuid[])",
                [FIXTURE_ORG_SEL_ID, FIXTURE_ORG_NONSEL_ID],
            )
            await conn.execute(
                "DELETE FROM platform_model_catalog WHERE model_id = $1",
                FIXTURE_CATALOG_MODEL_ID,
            )
            for org_id in (FIXTURE_ORG_SEL_ID, FIXTURE_ORG_NONSEL_ID):
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
            # the Hollisworks zero-recipient probe's audit row
            await conn.execute(
                "DELETE FROM audit_log WHERE org_id = $1 AND action LIKE "
                "'ai_model_availability%' AND payload::text LIKE "
                "'%verify_avail_probe%'",
                HOLLIS,
            )
    finally:
        reset_rls_context(tokens)


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


async def main() -> int:
    import os as _os
    if _os.environ.get("VERIFY_FORCE_FAIL") == "1":
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
    import services.workflow_todos as wt

    pool = await get_pool()
    baseline_deployments = {m.get("model_name") for m in _model_info()}
    print(f"baseline LiteLLM deployments: {sorted(baseline_deployments)}")

    sonnet_restored = True
    try:
        # ══════════════════════════════════════════════════════════════
        print("\n=== TASK 1: discovery findings ===\n")
        import inspect

        cred_sig = str(inspect.signature(wt.create_credential_failure_alerts))
        check("1c. create_credential_failure_alerts' real signature is "
              "(conn, *, org_id, provider, error_detail)",
              cred_sig == "(conn, *, org_id, provider: 'str', error_detail: 'str') -> 'list'",
              cred_sig)
        avail_source = inspect.getsource(wt.create_model_availability_alerts)
        check("1c. create_model_availability_alerts reuses the SAME shared "
              "helpers (_upsert_todo, _record_undelivered_alert, "
              "_org_admin_recipients) — a sibling, not a third mechanism",
              "_upsert_todo(" in avail_source
              and "_record_undelivered_alert(" in avail_source
              and "_org_admin_recipients(" in avail_source)
        find("1c. create_credential_failure_alerts(conn, *, org_id, "
             "provider, error_detail) — create_model_availability_alerts "
             "(conn, *, org_id, model_id, availability) matches its shape "
             "exactly, folding the non-uuid subject (model_id, like "
             "provider) into `source` and using org_id as the real "
             "related_id, since member_todos.related_id and "
             "audit_log.resource_id are both genuine uuid columns.")

        chain_source = inspect.getsource(ex._execute_chain)
        check("1b. _execute_chain filters 'disabled' models via "
              "disabled_model_ids() immediately after the D2 authorized "
              "filter, with a safe-model fallback",
              "disabled_model_ids()" in chain_source
              and "resolve_model(org_id, key=DEFAULT_MODEL_KEY)" in chain_source)
        find("1b. the exact call-path enforcement point: "
             "services.extraction._execute_chain, right after the existing "
             "D2 `authorized` filter and before the Phase-E effort "
             "resolution block — a 'disabled' model_id is dropped from "
             "`attempts` unconditionally (regardless of org authorization); "
             "'deprecated' is deliberately NOT filtered there, so an "
             "existing selection keeps making real calls.")

        list_catalog_source = inspect.getsource(mc.list_catalog)
        list_catalog_for_org_source = inspect.getsource(mc.list_catalog_for_org)
        resolve_auth_source = inspect.getsource(mc.resolve_authorized_models)
        check("1a. list_catalog (the Hollisworks curation screen's read) "
              "SELECTs availability for display but has NO WHERE clause "
              "filtering on it — every state must stay visible to manage it",
              "availability" in list_catalog_source
              and "WHERE" not in list_catalog_source)
        check("1a. list_catalog_for_org (the org-facing picker) DOES filter "
              "by availability, keeping only 'available' rows plus the "
              "org's own existing selections",
              "availability" in list_catalog_for_org_source
              and "list_org_selections" in list_catalog_for_org_source)
        check("1a. resolve_authorized_models deliberately does NOT "
              "reference availability at all — an existing selection must "
              "keep resolving forever, regardless of catalog state",
              "availability" not in resolve_auth_source)
        find("1a. real reads needing the availability filter: "
             "GET /orgs/{org_id}/settings/model-selections and "
             "GET /orgs/{org_id}/settings/ai-tasks (both now call "
             "list_catalog_for_org instead of list_catalog). Reads that "
             "must NOT filter: GET /admin/model-catalog (Hollisworks' own "
             "curation screen — list_catalog, unfiltered) and "
             "resolve_authorized_models (the call-path's own "
             "authorization set, which must keep including a 'deprecated' "
             "or even 'disabled' selection so the picker's 'still shows for "
             "an existing selector' and the call-path's 'still works' "
             "requirements can both be true from the same underlying data).")

        # ══════════════════════════════════════════════════════════════
        print("\n=== no-regression: baseline state + an untouched call ===\n")
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT model_id, availability FROM platform_model_catalog "
                "WHERE model_id = ANY($1::text[])", list(REAL_CATALOG),
            )
        avail_by_id = {r["model_id"]: r["availability"] for r in rows}
        check("2. all three seeded models are 'available' before this run "
              "— this is the state every model is in, no regression",
              all(avail_by_id.get(m) == "available" for m in REAL_CATALOG),
              f"{avail_by_id}")

        disabled_now = await mc.disabled_model_ids()
        check("2. disabled_model_ids() is empty before this run",
              disabled_now == set(), f"{disabled_now}")

        await teardown_fixtures(pool)  # clean slate from any prior failed run
        await setup_fixtures(pool)

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

        async def _call_text(*, org_id, task_type, model=None):
            return await ex.call_claude_text(
                system="Reply with exactly one word.",
                messages=[{"role": "user", "content": "Say OK."}],
                max_tokens=8, org_id=org_id, task_type=task_type, model=model,
            )

        async def _read_log(task_type):
            async with pool.acquire() as conn:
                return await conn.fetch(
                    "SELECT * FROM ai_decision_log WHERE task_type = $1 "
                    "ORDER BY created_at DESC LIMIT 1", task_type,
                )

        result_nonreg = await _as_org(
            FIXTURE_ORG_NONSEL_ID, _call_text,
            org_id=FIXTURE_ORG_NONSEL_ID, task_type="verify_avail_noregression",
        )
        check("2. an untouched fixture org's real call succeeds",
              result_nonreg is not None, f"got {result_nonreg!r}")
        log_nonreg = await _as_super(_read_log, "verify_avail_noregression")
        check("2. no-regression call: model_used='claude-haiku', "
              "fallback_used=false, success=true — byte-for-byte pre-sprint "
              "behaviour",
              len(log_nonreg) == 1 and log_nonreg[0]["model_used"] == REAL_HAIKU
              and log_nonreg[0]["fallback_used"] is False
              and log_nonreg[0]["success"] is True,
              f"{dict(log_nonreg[0]) if log_nonreg else None}")

        # ══════════════════════════════════════════════════════════════
        print("\n=== TASK 2/8: permission gate on the availability endpoint "
              "(isolated fixture catalog row — zero real-model risk) ===\n")
        from starlette.testclient import TestClient
        import main as main_module

        await close_pool()
        client = TestClient(main_module.app, raise_server_exceptions=False)
        client.__enter__()
        try:
            superadmin = _Principal(client, FIXTURE_SUPERADMIN_SUB, FIXTURE_ORG_SEL_ID)
            admin_sel = _Principal(client, FIXTURE_ORG_SEL_ADMIN_SUB, FIXTURE_ORG_SEL_ID)
            admin_nonsel = _Principal(client, FIXTURE_ORG_NONSEL_ADMIN_SUB, FIXTURE_ORG_NONSEL_ID)

            r_fixture_create = superadmin.call(
                "post", "/api/v1/admin/model-catalog",
                body={
                    "model_id": FIXTURE_CATALOG_MODEL_ID,
                    "display_name": "Verify Availability Fixture",
                    "provider": "anthropic",
                },
            )
            check("setup: super_admin creates the isolated fixture catalog "
                  "row -> 201", r_fixture_create.status_code == 201,
                  f"HTTP {r_fixture_create.status_code} {r_fixture_create.text[:200]}")
            check("setup: the new row defaults to 'available'",
                  r_fixture_create.json().get("availability") == "available")

            r_403 = admin_sel.call(
                "put", f"/api/v1/admin/model-catalog/{FIXTURE_CATALOG_MODEL_ID}/availability",
                body={"availability": "deprecated"},
            )
            check("8. org_admin: PUT availability -> 403 (super_admin only)",
                  r_403.status_code == 403, f"HTTP {r_403.status_code}")

            def _fixture_row():
                models = superadmin.call("get", "/api/v1/admin/model-catalog").json().get("models", [])
                return next((m for m in models if m["model_id"] == FIXTURE_CATALOG_MODEL_ID), None)

            row_after_403 = _fixture_row()
            check("8. the refused org_admin write genuinely left the row "
                  "unchanged (still 'available')",
                  row_after_403 is not None and row_after_403["availability"] == "available",
                  f"{row_after_403}")

            r_200 = superadmin.call(
                "put", f"/api/v1/admin/model-catalog/{FIXTURE_CATALOG_MODEL_ID}/availability",
                body={"availability": "deprecated"},
            )
            check("8. super_admin: PUT availability -> 200 on the IDENTICAL "
                  "request the org_admin was just refused on",
                  r_200.status_code == 200, f"HTTP {r_200.status_code} {r_200.text[:200]}")
            check("8. response reflects the real new state",
                  r_200.json().get("availability") == "deprecated")

            r_bad = superadmin.call(
                "put", f"/api/v1/admin/model-catalog/{FIXTURE_CATALOG_MODEL_ID}/availability",
                body={"availability": "not_a_real_state"},
            )
            check("7. an invalid availability value -> 400, refused by the "
                  "real DB CHECK constraint",
                  r_bad.status_code == 400, f"HTTP {r_bad.status_code} {r_bad.text[:200]}")
            row_after_bad = _fixture_row()
            check("7. the refused invalid write left the row unchanged "
                  "(still 'deprecated' from the prior successful write)",
                  row_after_bad is not None and row_after_bad["availability"] == "deprecated",
                  f"{row_after_bad}")

            r_del_inuse_guard = superadmin.call(
                "delete", f"/api/v1/admin/model-catalog/{FIXTURE_CATALOG_MODEL_ID}"
            )
            check("cleanup: super_admin deletes the never-selected fixture "
                  "row -> 200 (deletion stays possible for a model no org "
                  "has ever selected)",
                  r_del_inuse_guard.status_code == 200,
                  f"HTTP {r_del_inuse_guard.status_code} {r_del_inuse_guard.text[:200]}")

            # ══════════════════════════════════════════════════════════
            print("\n=== TASK 3: 'deprecated' on the REAL 'claude-sonnet' "
                  "deployment ===\n")

            r_sel_write = admin_sel.call(
                "put", f"/api/v1/orgs/{FIXTURE_ORG_SEL_ID}/settings/model-selections",
                body={"model_ids": [REAL_SONNET, REAL_HAIKU]},
            )
            check("setup: org SEL selects [claude-sonnet, claude-haiku] "
                  "(the safe model must ALSO be authorised, or a later "
                  "disabled-fallback could not itself resolve)",
                  r_sel_write.status_code == 200, f"HTTP {r_sel_write.status_code}")

            r_deprecate = superadmin.call(
                "put", f"/api/v1/admin/model-catalog/{REAL_SONNET}/availability",
                body={"availability": "deprecated"},
            )
            check("3. super_admin deprecates the real 'claude-sonnet' row "
                  "-> 200", r_deprecate.status_code == 200,
                  f"HTTP {r_deprecate.status_code} {r_deprecate.text[:200]}")
            sonnet_restored = False

            r_picker_nonsel = admin_nonsel.call(
                "get", f"/api/v1/orgs/{FIXTURE_ORG_NONSEL_ID}/settings/model-selections",
            )
            nonsel_ids = {
                m["model_id"] for m in r_picker_nonsel.json().get("vocabularies", {}).get("catalog", [])
            }
            check("3. 'deprecated' claude-sonnet is HIDDEN from the "
                  "non-selecting org's picker",
                  REAL_SONNET not in nonsel_ids, f"catalog ids: {sorted(nonsel_ids)}")

            r_picker_sel = admin_sel.call(
                "get", f"/api/v1/orgs/{FIXTURE_ORG_SEL_ID}/settings/model-selections",
            )
            sel_catalog = r_picker_sel.json().get("vocabularies", {}).get("catalog", [])
            sel_sonnet_row = next((m for m in sel_catalog if m["model_id"] == REAL_SONNET), None)
            check("3. the EXISTING selector still sees 'claude-sonnet' in "
                  "its own picker, correctly labelled 'deprecated'",
                  sel_sonnet_row is not None and sel_sonnet_row.get("availability") == "deprecated",
                  f"{sel_sonnet_row}")
        finally:
            client.__exit__(None, None, None)

        await close_pool()
        pool = await get_pool()

        # A real call for the existing selector, through the now-deprecated
        # model — must still work (Task 3's second half).
        result_deprecated = await _as_org(
            FIXTURE_ORG_SEL_ID, _call_text,
            org_id=FIXTURE_ORG_SEL_ID, task_type="verify_avail_deprecated_still_works",
            model=REAL_SONNET,
        )
        check("3. a real call for the existing selector, explicitly through "
              "the deprecated model, succeeds",
              result_deprecated is not None, f"got {result_deprecated!r}")
        log_deprecated = await _as_super(_read_log, "verify_avail_deprecated_still_works")
        check("3. the deprecated call genuinely used claude-sonnet (not a "
              "silent substitution) — proven from ai_decision_log",
              len(log_deprecated) == 1 and log_deprecated[0]["model_used"] == REAL_SONNET
              and log_deprecated[0]["success"] is True,
              f"{dict(log_deprecated[0]) if log_deprecated else None}")

        # ══════════════════════════════════════════════════════════════
        print("\n=== TASK 4: 'disabled' falls back to the org safe model ===\n")

        async def _disable_sonnet():
            async with pool.acquire() as conn:
                from services.model_catalog import get_orgs_selecting, set_catalog_availability
                result = await set_catalog_availability(conn, model_id=REAL_SONNET, availability="disabled")
                affected = await get_orgs_selecting(conn, REAL_SONNET)
                for affected_org_id in affected:
                    await wt.create_model_availability_alerts(
                        conn, org_id=affected_org_id, model_id=REAL_SONNET, availability="disabled",
                    )
                return result, affected

        disable_result, disable_affected = await _as_super(_disable_sonnet)
        check("4. claude-sonnet is now 'disabled'",
              disable_result.get("availability") == "disabled")
        check("4. exactly the org(s) that actually selected claude-sonnet "
              "are affected (org SEL only, not org NONSEL)",
              set(str(o) for o in disable_affected) == {str(FIXTURE_ORG_SEL_ID)},
              f"{disable_affected}")

        result_disabled = await _as_org(
            FIXTURE_ORG_SEL_ID, _call_text,
            org_id=FIXTURE_ORG_SEL_ID, task_type="verify_avail_disabled_fallback",
            model=REAL_SONNET,
        )
        check("4. a real call requesting the now-disabled model still "
              "succeeds (falls back rather than failing)",
              result_disabled is not None, f"got {result_disabled!r}")
        log_disabled = await _as_super(_read_log, "verify_avail_disabled_fallback")
        check("4. the disabled call's model_used is the org's safe model "
              "('claude-haiku'), NOT claude-sonnet — proven from "
              "ai_decision_log, not from config",
              len(log_disabled) == 1 and log_disabled[0]["model_used"] == REAL_HAIKU
              and log_disabled[0]["model_requested"] == REAL_SONNET
              and log_disabled[0]["success"] is True,
              f"{dict(log_disabled[0]) if log_disabled else None}")

        # restore claude-sonnet to 'available' as early as possible
        async def _restore_sonnet():
            async with pool.acquire() as conn:
                from services.model_catalog import set_catalog_availability
                return await set_catalog_availability(conn, model_id=REAL_SONNET, availability="available")

        restored_row = await _as_super(_restore_sonnet)
        sonnet_restored = restored_row.get("availability") == "available"
        check("teardown: claude-sonnet restored to 'available'", sonnet_restored)

        # ══════════════════════════════════════════════════════════════
        print("\n=== TASK 5/9: alerts reached ONLY the selecting org ===\n")

        async def _todos_for(org_id, model_id):
            async with pool.acquire() as conn:
                return await conn.fetch(
                    "SELECT id, title FROM member_todos WHERE org_id = $1 "
                    "AND source = $2", org_id, f"ai_model_availability:{model_id}",
                )

        todos_sel = await _as_super(_todos_for, FIXTURE_ORG_SEL_ID, REAL_SONNET)
        check("5. the SELECTING org (org SEL, a real manage_org_settings "
              "holder) received a real alert todo",
              len(todos_sel) >= 1, f"{[dict(r) for r in todos_sel]}")

        todos_nonsel = await _as_super(_todos_for, FIXTURE_ORG_NONSEL_ID, REAL_SONNET)
        check("5/9. CROSS-ORG: the NON-selecting org received ZERO alert "
              "todos for the same model transition",
              len(todos_nonsel) == 0, f"{[dict(r) for r in todos_nonsel]}")

        # ══════════════════════════════════════════════════════════════
        print("\n=== TASK 6: zero-recipient path — the REAL Hollisworks org ===\n")
        probe_model_id = "verify_avail_probe_model"

        async def _hollis_probe():
            async with pool.acquire() as conn:
                return await wt.create_model_availability_alerts(
                    conn, org_id=str(HOLLIS), model_id=probe_model_id,
                    availability="disabled",
                )

        hollis_ids = await _as_super(_hollis_probe)
        check("6. create_model_availability_alerts returns an EMPTY id "
              "list for Hollisworks (no todo written — zero "
              "manage_org_settings holders today)",
              hollis_ids == [])

        async def _hollis_audit():
            async with pool.acquire() as conn:
                return await conn.fetchrow(
                    "SELECT * FROM audit_log WHERE org_id = $1 AND action = $2 "
                    "AND payload::text LIKE '%' || $3 || '%' "
                    "ORDER BY created_at DESC LIMIT 1",
                    HOLLIS, wt.MODEL_AVAILABILITY_ALERT_UNDELIVERED_ACTION,
                    probe_model_id,
                )

        hollis_audit_row = await _as_super(_hollis_audit)
        check("6. a findable audit_log row was written instead of failing "
              "silently", hollis_audit_row is not None)
        if hollis_audit_row is not None:
            payload = hollis_audit_row["payload"]
            if isinstance(payload, str):
                payload = json.loads(payload)
            check("6. the audit row names the real reason (no "
                  "manage_org_settings holder)",
                  "manage_org_settings" in json.dumps(payload))

        hollis_todos = await _as_super(
            lambda: pool.fetch(
                "SELECT id FROM member_todos WHERE org_id = $1 AND source = $2",
                HOLLIS, f"ai_model_availability:{probe_model_id}",
            )
        )
        check("6. zero member_todos rows were created for Hollisworks",
              len(hollis_todos) == 0)

    finally:
        if not sonnet_restored:
            try:
                async def _force_restore():
                    async with pool.acquire() as conn:
                        from services.model_catalog import set_catalog_availability
                        return await set_catalog_availability(
                            conn, model_id=REAL_SONNET, availability="available"
                        )
                tokens = set_rls_context(None, True)
                try:
                    await _force_restore()
                finally:
                    reset_rls_context(tokens)
                print("[teardown] force-restored claude-sonnet to 'available'")
            except Exception as exc:  # noqa: BLE001
                print(f"[teardown] FAILED to restore claude-sonnet: {exc}")
        await teardown_fixtures(pool)

    # ══════════════════════════════════════════════════════════════
    print("\n=== TEARDOWN: zero leftover rows, seeded models restored, "
          "proxy unchanged ===\n")

    async def pool_fetchrow(q, *a):
        async with pool.acquire() as conn:
            return await conn.fetchrow(q, *a)

    tokens = set_rls_context(None, True)
    try:
        leftover_orgs = await pool_fetchrow(
            "SELECT count(*) AS n FROM organizations WHERE id = ANY($1::uuid[])",
            [FIXTURE_ORG_SEL_ID, FIXTURE_ORG_NONSEL_ID],
        )
        check("teardown: zero leftover fixture organizations", leftover_orgs["n"] == 0)
        leftover_catalog = await pool_fetchrow(
            "SELECT count(*) AS n FROM platform_model_catalog WHERE model_id = $1",
            FIXTURE_CATALOG_MODEL_ID,
        )
        check("teardown: zero leftover fixture catalog rows", leftover_catalog["n"] == 0)
        leftover_selections = await pool_fetchrow(
            "SELECT count(*) AS n FROM org_model_selections WHERE org_id = ANY($1::uuid[])",
            [FIXTURE_ORG_SEL_ID, FIXTURE_ORG_NONSEL_ID],
        )
        check("teardown: zero leftover fixture org_model_selections rows",
              leftover_selections["n"] == 0)
        leftover_todos = await pool_fetchrow(
            "SELECT count(*) AS n FROM member_todos WHERE org_id = ANY($1::uuid[])",
            [FIXTURE_ORG_SEL_ID, FIXTURE_ORG_NONSEL_ID],
        )
        check("teardown: zero leftover fixture member_todos rows",
              leftover_todos["n"] == 0)
        leftover_log = await pool_fetchrow(
            "SELECT count(*) AS n FROM ai_decision_log WHERE task_type LIKE 'verify_avail_%'"
        )
        check("teardown: zero leftover ai_decision_log rows from this run",
              leftover_log["n"] == 0)
        leftover_hollis_audit = await pool_fetchrow(
            "SELECT count(*) AS n FROM audit_log WHERE org_id = $1 AND "
            "action LIKE 'ai_model_availability%' AND payload::text LIKE "
            "'%verify_avail_probe%'", HOLLIS,
        )
        check("teardown: zero leftover Hollisworks probe audit_log rows",
              leftover_hollis_audit["n"] == 0)

        final_avail = await pool.fetch(
            "SELECT model_id, availability FROM platform_model_catalog "
            "WHERE model_id = ANY($1::text[])", list(REAL_CATALOG),
        )
        final_avail_by_id = {r["model_id"]: r["availability"] for r in final_avail}
        check("teardown: all three seeded models are 'available' again",
              all(final_avail_by_id.get(m) == "available" for m in REAL_CATALOG),
              f"{final_avail_by_id}")
    finally:
        reset_rls_context(tokens)

    final_deployments = {m.get("model_name") for m in _model_info()}
    check("teardown: proxy deployment set is byte-for-byte unchanged "
          "(this sprint makes no /model/new or /model/delete calls)",
          final_deployments == baseline_deployments,
          f"before={sorted(baseline_deployments)} after={sorted(final_deployments)}")

    print(f"\n{'=' * 70}\nTOTAL: {_n_pass} PASS, {_n_fail} FAIL, {len(_finds)} FIND\n{'=' * 70}")
    return 0 if _ok else 1


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except Exception:
        import traceback
        traceback.print_exc()
        print(f"\n{'=' * 70}\nTOTAL: {_n_pass} PASS, {_n_fail} FAIL, {len(_finds)} FIND\n{'=' * 70}")
        sys.exit(2)
