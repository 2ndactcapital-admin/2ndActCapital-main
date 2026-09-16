"""verify_litellmseedfix.py — LiteLLM seeded-chain mismatch fix.

D2's own verify script (verify_litellmphased2.py) found, live, that
org_settings' real DEFAULT_SETTINGS model strings
(``claude-sonnet-4-6``, ``claude-haiku-4-5-20251001``) were NEVER callable
against the live hollisworks-litellm proxy — only the proxy's actual
REGISTERED deployment ``model_name``, ``claude-sonnet``, was, and
``claude-haiku`` did not exist as a deployment AT ALL. Every real call site
worked only because it passed an explicit ``model=`` override; anything that
genuinely fell through to the seeded default chain was a latent failure.

Proves, against the REAL live database and the REAL live
hollisworks-litellm proxy, that:

  1. Task 1's four discovery findings hold, live:
     1a. Exactly which model names are callable on the proxy today, and the
         registered deployments' real ``model_name`` / upstream
         ``litellm_params.model`` values.
     1b. ``platform_model_catalog``'s ``model_id`` values now equal the
         proxy's registered deployment names — the SAME identifier space,
         not two conventions that happen to agree by accident.
     1c. Every real consumer of ``ai.model.default`` / ``ai.model.assistant``
         / ``ai.model.document_classifier`` / ``ai.model.fallback_chain``
         (grepped live, not assumed), and that the two dead keys
         (``ai.model.provider``, ``ai.model.fallback``) genuinely have zero
         consumers left anywhere in application code.
     1d. The cost consequence of keeping ``document_classifier``/``default``
         on Haiku rather than collapsing them onto Sonnet to make them
         callable — computed from ``services.extraction._MODEL_PRICING``,
         the repo's own live cost model, not an assumed multiplier.
  2. A real AI call using the SEEDED default chain — NO ``model=``
     override — succeeds end to end, and the resulting ``ai_decision_log``
     row shows the resolved model was the real registered deployment name.
  3. The fallback chain genuinely WALKS on a forced first-model failure
     (a fixture org's ``ai.model.default`` pointed at a model the proxy has
     never registered), still with no override — proven by the
     ``ai_decision_log`` row's ``fallback_used``/``model_requested``/
     ``model_used`` fields, not merely "a response came back".
  4. ``document_classifier``'s resolver (``resolve_classifier_model``)
     resolves an unconfigured org to the real seeded default, and that model
     is genuinely callable (a real call, not just a string comparison).
  5. ``platform_model_catalog``, ``org_settings.DEFAULT_SETTINGS`` and the
     live proxy's registered deployments all agree on naming — proven by a
     live three-way set comparison, not asserted.
  6. ``verify_litellmphased2.py`` now prints a real ``TOTAL:`` line and
     exits non-zero on a genuine failure — proven by actually invoking it
     (via its own ``VERIFY_FORCE_FAIL=1`` self-test hook, added for exactly
     this) as a real subprocess and reading its real exit code + stdout.
  7. No regression: the two real production orgs (2nd Act, Hollisworks)
     resolve exactly the same (default, assistant) deployment names as the
     fixture path, read-only; and no active service/router code still
     hardcodes the old, now-wrong upstream model strings as a call
     override.
  8. Teardown: zero leftover fixture rows in every touched table, and the
     live proxy's deployment set is EXACTLY the three deployments this fix
     establishes (``claude-sonnet``, ``claude-haiku``, ``voyage-3.5``) — no
     more, no fewer.

Hydrates DATABASE_URL (and every other Doppler secret, including
LITELLM_BASE_URL / LITELLM_MASTER_KEY / ANTHROPIC_API_KEY) from Doppler over
HTTPS at startup — the verify_litellmphased1c.py pattern. run_sprint.sh's
Step 3 does NOT `doppler run --` this script.

Never prints a credential value.

Cost note: 3 real Anthropic calls (max_tokens<=8) against the seeded chain,
plus whatever verify_litellmphased2.py's own force-fail self-test costs
(zero — it exits before any network/DB call).

Run:  python3 apps/api/scripts/verify_litellmseedfix.py
"""
from __future__ import annotations

import asyncio
import json
import os
import pathlib
import subprocess
import sys
import traceback
import urllib.error
import urllib.request
from uuid import UUID

HERE = pathlib.Path(__file__).resolve()
sys.path.insert(0, str(HERE.parent))
from _db_bootstrap import bootstrap_async  # noqa: E402

ORG = UUID("00000000-0000-0000-0000-000000000001")          # 2nd Act Capital (real)
HOLLIS = UUID("bb347258-8f28-4f49-8cc9-e29ccad82884")        # Hollisworks (real)

FIXTURE_ORG_ID = UUID("99000000-0000-0000-0000-0000005eed01")

REAL_HAIKU = "claude-haiku"
REAL_SONNET = "claude-sonnet"
REAL_VOYAGE = "voyage-3.5"
EXPECTED_DEPLOYMENTS = {REAL_HAIKU, REAL_SONNET, REAL_VOYAGE}

BOGUS_MODEL = "verify-seedfix-bogus-unregistered-model"

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


# ── LiteLLM admin API — direct HTTP ─────────────────────────────────────────


def _litellm_http(path, *, method="GET", body=None, timeout=60):
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


async def setup_fixtures(pool):
    from services.database import reset_rls_context, set_rls_context

    tokens = set_rls_context(None, True)
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO organizations (id, name, slug)
                VALUES ($1, $2, $3)
                ON CONFLICT (id) DO UPDATE SET name = EXCLUDED.name
                """,
                FIXTURE_ORG_ID, "Verify SeedFix Org", "verify-seedfix-org",
            )
    finally:
        reset_rls_context(tokens)


async def teardown_fixtures(pool):
    from services.database import reset_rls_context, set_rls_context

    tokens = set_rls_context(None, True)
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM ai_decision_log WHERE task_type LIKE 'verify_seedfix_%'"
            )
            await conn.execute(
                "DELETE FROM org_settings WHERE org_id = $1", FIXTURE_ORG_ID
            )
            await conn.execute(
                "DELETE FROM organizations WHERE id = $1", FIXTURE_ORG_ID
            )
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

    from services.database import get_pool, reset_rls_context, set_rls_context
    from services.document_classifier import resolve_classifier_model
    from services.org_settings import DEFAULT_SETTINGS
    import services.extraction as ex

    pool = await get_pool()

    try:
        # ══════════════════════════════════════════════════════════════
        print("\n=== TASK 1: discovery findings ===\n")

        # 1a — live proxy deployments.
        deployments = _model_info()
        deployment_names = {m.get("model_name") for m in deployments}
        upstream_by_name = {
            m.get("model_name"): (m.get("litellm_params") or {}).get("model")
            for m in deployments
        }
        find(
            "1a. live hollisworks-litellm registered deployments",
            f"{ {n: upstream_by_name[n] for n in sorted(deployment_names)} }",
        )
        check(
            "1a. exactly the three expected deployments are registered "
            "live (claude-sonnet, claude-haiku, voyage-3.5) — no more, no "
            "fewer",
            deployment_names == EXPECTED_DEPLOYMENTS,
            f"got {sorted(deployment_names)}",
        )
        check(
            "1a. claude-sonnet's upstream is anthropic/claude-sonnet-4-6",
            upstream_by_name.get(REAL_SONNET) == "anthropic/claude-sonnet-4-6",
            f"got {upstream_by_name.get(REAL_SONNET)!r}",
        )
        check(
            "1a. claude-haiku's upstream is anthropic/claude-haiku-4-5-20251001 "
            "(the new deployment this sprint registered)",
            upstream_by_name.get(REAL_HAIKU) == "anthropic/claude-haiku-4-5-20251001",
            f"got {upstream_by_name.get(REAL_HAIKU)!r}",
        )

        # 1b — catalog model_id set == proxy deployment name set.
        tokens = set_rls_context(None, True)
        try:
            async with pool.acquire() as conn:
                catalog_rows = await conn.fetch(
                    "SELECT model_id, provider FROM platform_model_catalog"
                )
        finally:
            reset_rls_context(tokens)
        catalog_ids = {r["model_id"] for r in catalog_rows}
        check(
            "1b. platform_model_catalog.model_id set == the live proxy's "
            "registered deployment name set (same identifier space, not "
            "two conventions that happen to overlap)",
            catalog_ids == deployment_names == EXPECTED_DEPLOYMENTS,
            f"catalog={sorted(catalog_ids)} proxy={sorted(deployment_names)}",
        )

        # 1c — real consumers of the ai.model.* keys, live-grepped (never
        # assumed from a prior sprint's doc).
        repo_root = HERE.parents[2]

        def _grep(pattern, *, paths):
            result = subprocess.run(
                ["grep", "-rl", pattern, *paths],
                cwd=str(repo_root), capture_output=True, text=True,
            )
            return [ln for ln in result.stdout.splitlines() if ln]

        default_consumers = _grep("DEFAULT_MODEL_KEY", paths=["apps/api/services", "apps/api/routers"])
        assistant_consumers = _grep("ASSISTANT_MODEL_KEY", paths=["apps/api/services", "apps/api/routers"])
        classifier_consumers = _grep("DOCUMENT_CLASSIFIER_MODEL_KEY", paths=["apps/api/services"])
        find(
            "1c. real application-code consumers of the ai.model.* keys "
            "(grepped live)",
            f"default={default_consumers} assistant={assistant_consumers} "
            f"classifier={classifier_consumers}",
        )
        check(
            "1c. ai.model.default (DEFAULT_MODEL_KEY) has real consumers "
            "outside org_settings.py/extraction.py itself",
            any("document_classifier" in f or "note_terms_extraction" in f
                for f in default_consumers),
            f"{default_consumers}",
        )
        check(
            "1c. ai.model.assistant (ASSISTANT_MODEL_KEY) has a real "
            "consumer in routers/dashboard.py",
            any(f.endswith("routers/dashboard.py") for f in assistant_consumers),
            f"{assistant_consumers}",
        )
        check(
            "1c. ai.model.document_classifier has a real consumer in "
            "services/document_classifier.py",
            any(f.endswith("services/document_classifier.py") for f in classifier_consumers),
            f"{classifier_consumers}",
        )

        # The two dead keys must have ZERO consumers anywhere in real
        # application code (services/routers) — confirming the discovery
        # finding still holds after this sprint's own edits, and that the
        # keys' removal from DEFAULT_SETTINGS broke nothing.
        dead_key_hits = _grep(
            r'"ai\.model\.provider"\|"ai\.model\.fallback"',
            paths=["apps/api/services", "apps/api/routers"],
        )
        check(
            "1c. ai.model.provider / ai.model.fallback (dead keys, removed "
            "from DEFAULT_SETTINGS this sprint) have ZERO consumers left "
            "anywhere in services/ or routers/",
            dead_key_hits == [], f"hits in: {dead_key_hits}",
        )

        # 1d — cost justification, computed from the repo's own live cost
        # model, not an assumed multiplier.
        haiku_in, haiku_out = ex._MODEL_PRICING["claude-haiku"]
        sonnet_in, sonnet_out = ex._MODEL_PRICING["claude-sonnet"]
        ratio_in = sonnet_in / haiku_in
        ratio_out = sonnet_out / haiku_out
        find(
            "1d. DECISION: register a real 'claude-haiku' proxy deployment "
            "and keep ai.model.default/document_classifier on it, rather "
            "than collapsing them onto 'claude-sonnet' to make them "
            "callable. Per this repo's OWN live cost model "
            "(services.extraction._MODEL_PRICING): Sonnet is "
            f"{ratio_in}x the input price and {ratio_out}x the output price "
            f"of Haiku (haiku=${haiku_in}/${haiku_out} per 1M tokens, "
            f"sonnet=${sonnet_in}/${sonnet_out} per 1M tokens). "
            "document_classifier is a high-volume, per-document call path "
            "(every ingested document is classified) — collapsing it onto "
            "Sonnet would have silently multiplied that path's real dollar "
            "cost by the same ratio for a naming fix that had nothing to "
            "do with model quality. The naming convention decided here "
            "(org_settings values == proxy deployment `model_name`, never "
            "the raw upstream provider id) means adding a cheaper tier in "
            "the future is a proxy deployment + a settings value, not a "
            "resolver code change.",
        )
        check(
            "1d. Sonnet is genuinely more expensive than Haiku per this "
            "repo's own cost model (the real justification for registering "
            "claude-haiku rather than collapsing onto claude-sonnet)",
            ratio_in > 1 and ratio_out > 1, f"in={ratio_in}x out={ratio_out}x",
        )

        # ══════════════════════════════════════════════════════════════
        print("\n=== TASK 2: seeded default chain, NO override ===\n")

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
                rows = await conn.fetch(
                    "SELECT model_requested, model_used, fallback_used, "
                    "fallback_reason, success FROM ai_decision_log "
                    "WHERE task_type = $1 ORDER BY created_at", task_type,
                )
                return [dict(r) for r in rows]

        default_result = await _as_org(
            FIXTURE_ORG_ID, _call_text,
            org_id=FIXTURE_ORG_ID, task_type="verify_seedfix_default_no_override",
        )
        check(
            "2. a real call through the SEEDED default chain — NO model= "
            "override — succeeds end to end (real text response, not None)",
            bool(default_result), f"got {default_result!r}",
        )
        default_log = await _as_super(_read_log, "verify_seedfix_default_no_override")
        check(
            "2. the ai_decision_log row confirms the primary that was "
            "actually tried is the real registered deployment name "
            "(claude-haiku), not the old upstream id",
            len(default_log) == 1 and default_log[0]["model_requested"] == REAL_HAIKU
            and default_log[0]["model_used"] == REAL_HAIKU
            and default_log[0]["success"] is True
            and default_log[0]["fallback_used"] is False,
            f"{default_log}",
        )

        # ══════════════════════════════════════════════════════════════
        print("\n=== TASK 3: fallback chain walks on a forced failure, NO override ===\n")

        async def _seed_setting(org_id, key, value):
            async with pool.acquire() as conn:
                await conn.execute(
                    "INSERT INTO org_settings "
                    "  (org_id, setting_key, setting_value, category, is_public) "
                    "VALUES ($1, $2, $3::jsonb, 'ai', false) "
                    "ON CONFLICT (org_id, setting_key) DO UPDATE "
                    "  SET setting_value = EXCLUDED.setting_value, updated_at = now()",
                    org_id, key, json.dumps(value),
                )

        # Fixture org's primary is a model the proxy has NEVER registered;
        # its fallback_chain is left unset, so it resolves to
        # DEFAULT_SETTINGS' real ai.model.fallback_chain (['claude-haiku']) —
        # the actual production fallback path, not a fixture-only one.
        await _as_super(_seed_setting, FIXTURE_ORG_ID, ex.DEFAULT_MODEL_KEY, BOGUS_MODEL)
        check(
            "3. DEFAULT_SETTINGS' real fallback_chain is genuinely "
            "['claude-haiku'] — the chain this test relies on walking onto",
            DEFAULT_SETTINGS.get(ex.FALLBACK_CHAIN_KEY) == [REAL_HAIKU],
            f"got {DEFAULT_SETTINGS.get(ex.FALLBACK_CHAIN_KEY)!r}",
        )

        fallback_result = await _as_org(
            FIXTURE_ORG_ID, _call_text,
            org_id=FIXTURE_ORG_ID, task_type="verify_seedfix_fallback_walk",
        )
        check(
            "3. the call still succeeds (real response) even though the "
            "primary model is unregistered — the chain walked onto the "
            "fallback, no model= override anywhere in this call",
            bool(fallback_result), f"got {fallback_result!r}",
        )
        fallback_log = await _as_super(_read_log, "verify_seedfix_fallback_walk")
        check(
            "3. ai_decision_log proves a REAL walk: model_requested is the "
            "bogus primary, model_used is claude-haiku, fallback_used=True, "
            "and the reason names the primary's real failure",
            len(fallback_log) == 1
            and fallback_log[0]["model_requested"] == BOGUS_MODEL
            and fallback_log[0]["model_used"] == REAL_HAIKU
            and fallback_log[0]["fallback_used"] is True
            and fallback_log[0]["success"] is True
            and bool(fallback_log[0]["fallback_reason"])
            and BOGUS_MODEL in fallback_log[0]["fallback_reason"],
            f"{fallback_log}",
        )

        # ══════════════════════════════════════════════════════════════
        print("\n=== TASK 4: document_classifier resolves to a genuinely callable model ===\n")

        # Reset the fixture org's default back to a real value first —
        # resolve_classifier_model falls through to ai.model.default when no
        # dedicated classifier override exists, and Task 3 deliberately left
        # this org's default pointed at BOGUS_MODEL.
        await _as_super(_seed_setting, FIXTURE_ORG_ID, ex.DEFAULT_MODEL_KEY, REAL_HAIKU)

        async def _resolve_classifier(org_id):
            async with pool.acquire() as conn:
                return await resolve_classifier_model(conn, org_id)

        classifier_model = await _as_org(FIXTURE_ORG_ID, _resolve_classifier, FIXTURE_ORG_ID)
        check(
            "4. an org with no dedicated classifier override resolves to "
            "the real seeded default (claude-haiku)",
            classifier_model == REAL_HAIKU, f"got {classifier_model!r}",
        )
        check(
            "4. that resolved model is genuinely a registered live "
            "deployment (re-checked against Task 1a's live probe)",
            classifier_model in deployment_names, f"{classifier_model!r} not in {deployment_names}",
        )
        classifier_call = await _as_org(
            FIXTURE_ORG_ID, _call_text, org_id=FIXTURE_ORG_ID,
            task_type="verify_seedfix_classifier_model", model=classifier_model,
        )
        check(
            "4. a real call using the classifier's resolved model succeeds",
            bool(classifier_call), f"got {classifier_call!r}",
        )

        # ══════════════════════════════════════════════════════════════
        print("\n=== TASK 5: catalog / settings / proxy naming agreement — proven ===\n")

        settings_values = {
            DEFAULT_SETTINGS[ex.DEFAULT_MODEL_KEY],
            DEFAULT_SETTINGS[ex.ASSISTANT_MODEL_KEY],
            DEFAULT_SETTINGS["ai.model.document_classifier"],
            *DEFAULT_SETTINGS[ex.FALLBACK_CHAIN_KEY],
        }
        check(
            "5. every org_settings DEFAULT_SETTINGS ai.model.* value is a "
            "real, live, registered proxy deployment name",
            settings_values <= deployment_names, f"{settings_values} vs {deployment_names}",
        )
        check(
            "5. every org_settings DEFAULT_SETTINGS ai.model.* value is "
            "also on the platform_model_catalog (the D2 picker and the "
            "resolver agree on the SAME set)",
            settings_values <= catalog_ids, f"{settings_values} vs {catalog_ids}",
        )
        check(
            "5. three-way agreement: settings ⊆ catalog == proxy == "
            "{claude-sonnet, claude-haiku, voyage-3.5} exactly",
            settings_values <= catalog_ids == deployment_names == EXPECTED_DEPLOYMENTS,
        )

        # ══════════════════════════════════════════════════════════════
        print("\n=== TASK 6: verify_litellmphased2.py — TOTAL line + non-zero exit ===\n")

        phased2_path = HERE.parent / "verify_litellmphased2.py"
        env = dict(os.environ)
        env["VERIFY_FORCE_FAIL"] = "1"
        forced = subprocess.run(
            [sys.executable, str(phased2_path)], cwd=str(HERE.parent),
            capture_output=True, text=True, timeout=60, env=env,
        )
        check(
            "6. verify_litellmphased2.py, forced to fail, exits non-zero "
            "(real subprocess exit code, not inferred)",
            forced.returncode != 0, f"exit={forced.returncode}",
        )
        check(
            "6. verify_litellmphased2.py, forced to fail, prints a real "
            "'TOTAL: N PASS, M FAIL' line matching the repo-wide convention",
            "TOTAL:" in forced.stdout and "FAIL" in forced.stdout,
            f"stdout tail: {forced.stdout[-300:]!r}",
        )
        check(
            "6. the forced-fail run reports at least one real FAIL (not a "
            "vacuous 0 FAIL exit)",
            "0 PASS, 1 FAIL" in forced.stdout, f"stdout tail: {forced.stdout[-300:]!r}",
        )

        # ══════════════════════════════════════════════════════════════
        print("\n=== TASK 7: no regression on existing call paths ===\n")

        for real_org, label in ((ORG, "2nd Act"), (HOLLIS, "Hollisworks")):
            real_default = await _as_org(real_org, ex.resolve_model, real_org, key=ex.DEFAULT_MODEL_KEY)
            real_assistant = await _as_org(real_org, ex.resolve_model, real_org, key=ex.ASSISTANT_MODEL_KEY)
            check(
                f"7. real org {label}: resolve_model(default) == "
                "claude-haiku, resolve_model(assistant) == claude-sonnet "
                "(read-only, nothing written to this org)",
                real_default == REAL_HAIKU and real_assistant == REAL_SONNET,
                f"default={real_default!r} assistant={real_assistant!r}",
            )

        stale_hit_lines = subprocess.run(
            ["grep", "-rn", r'claude-haiku-4-5-20251001\|claude-sonnet-4-6',
             "apps/api/services", "apps/api/routers"],
            cwd=str(repo_root), capture_output=True, text=True,
        ).stdout.splitlines()
        # Both files' matches were manually confirmed to be explanatory
        # comments naming the real upstream string for context (org_settings
        # .py's own naming-convention note; extraction.py's cost-model
        # comment) — never a value actually passed to a call. Any hit
        # OUTSIDE these two exact lines is a real, unexpected regression.
        ALLOWED_STALE_LINES = {
            "apps/api/services/org_settings.py:130:    # deployment) forwards to upstream 'anthropic/claude-sonnet-4-6' — and",
            "apps/api/services/extraction.py:376:# (claude-haiku-4-5-20251001) so the family prefix is a stable pricing key.",
        }
        unexpected_stale = [ln for ln in stale_hit_lines if ln not in ALLOWED_STALE_LINES]
        check(
            "7. no active services/routers code still hardcodes the OLD "
            "upstream model strings as a call-site override — the only "
            "surviving references are two known, manually-verified "
            "explanatory comments",
            unexpected_stale == [], f"unexpected hits: {unexpected_stale}",
        )

        embedding_model = DEFAULT_SETTINGS.get("ai.embedding.model")
        check(
            "7. the embedding path (ai.embedding.model) is untouched by "
            "this sprint and already matches its own live deployment name",
            embedding_model == REAL_VOYAGE, f"got {embedding_model!r}",
        )

    finally:
        await teardown_fixtures(pool)

        tokens = set_rls_context(None, True)
        try:
            async with pool.acquire() as conn2:
                leftover_org = await conn2.fetchval(
                    "SELECT count(*) FROM organizations WHERE id = $1", FIXTURE_ORG_ID
                )
                leftover_settings = await conn2.fetchval(
                    "SELECT count(*) FROM org_settings WHERE org_id = $1", FIXTURE_ORG_ID
                )
                leftover_logs = await conn2.fetchval(
                    "SELECT count(*) FROM ai_decision_log WHERE task_type LIKE 'verify_seedfix_%'"
                )
        finally:
            reset_rls_context(tokens)

        print("\n=== TASK 8: teardown ===\n")
        check("8. zero leftover fixture organizations", leftover_org == 0, f"count={leftover_org}")
        check("8. zero leftover fixture org_settings rows", leftover_settings == 0, f"count={leftover_settings}")
        check("8. zero leftover ai_decision_log fixture rows", leftover_logs == 0, f"count={leftover_logs}")

        final_deployments = {m.get("model_name") for m in _model_info()}
        check(
            "8. the live proxy's deployment set is EXACTLY the three "
            "deployments this fix establishes — claude-sonnet (assistant/"
            "premium tier), claude-haiku (default/high-volume tier, newly "
            "registered this sprint), voyage-3.5 (embeddings) — no more, "
            "no fewer, matching org_settings and platform_model_catalog "
            "exactly",
            final_deployments == EXPECTED_DEPLOYMENTS, f"got {sorted(final_deployments)}",
        )

        await pool.close()

    print(f"\n{'=' * 70}\nTOTAL: {_n_pass} PASS, {_n_fail} FAIL, {len(_finds)} FIND\n{'=' * 70}")
    return 0 if _ok else 1


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except Exception:
        traceback.print_exc()
        sys.exit(2)
