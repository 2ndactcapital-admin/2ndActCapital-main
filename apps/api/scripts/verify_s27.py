"""verify_s27.py — Sprint 27 (TaskRouter): per-org fallback CHAIN + decision log.

Pass/fail only. No interactive prompts. Idempotent. Teardown-at-start AND
teardown-at-end.

WHAT SPRINT 27 CHANGED
----------------------
The central AI-calling mechanism discovered in Task 1 lives entirely in
``apps/api/services/extraction.py``:

  * three public helpers — ``call_claude_json`` / ``call_claude_text`` /
    ``call_claude_with_tools`` — are the ONLY code that constructs an Anthropic
    client and calls ``.messages.create``. Zero call sites bypass them (Mini-
    Bedrock's goal, still true — asserted by the sweep below).
  * ``resolve_model(org_id, key)`` resolves WHICH model from org_settings; the
    old ``ai.model.fallback`` was a SINGLE string that no code consumed.

Sprint 27 replaces that dead single value with a real ordered chain
(``ai.model.fallback_chain``, a JSON array) that the new ``_execute_chain``
walks — primary first, then each fallback — logging every call to
``ai_decision_log`` (non-blocking).

HERMETIC BY DESIGN
------------------
There is no live ANTHROPIC_API_KEY in CI/local shells, and we must not make a
paid network call to prove routing logic. So this script injects a FAKE
``anthropic`` module (the exact seam ``_execute_chain`` constructs its client
from) whose ``messages.create`` succeeds or fails based on the model id. This
exercises the REAL chain-walking, REAL cost computation (from token usage), REAL
latency timing, and REAL ai_decision_log writes — only the network boundary is
faked. A model id containing ``BROKEN`` raises (simulating a primary-model
outage); anything else returns a small message with realistic token usage.

Column names are taken from docs/schema_snapshot.sql:
  ai_decision_log(id, org_id, task_type, model_requested, model_used,
    fallback_used bool, fallback_reason, cost_usd numeric, latency_ms int,
    success bool, error_detail, created_at)
  org_settings(id, org_id, setting_key, setting_value jsonb, category,
    is_public, updated_at, updated_by, created_at)  -- NOT bitemporal, upsert.

S28 FIX 1 — RLS IS ENFORCED NOW
-------------------------------
``DATABASE_URL`` connects as ``app_service`` (rolbypassrls = false). Every
fixture write/read on this script's own connection goes through ``platform()``
(one transaction, ``set_config('app.is_super_admin', 'true', true)`` — the same
helper the current verifies use); fixture orgs are seeded ``is_test = true``.
Every AI call / resolver runs under ``set_rls_context(org, False)`` — the
context ``rls_context_middleware`` gives a real request — so the pool can read
the org's own org_settings and write its own ai_decision_log row. Every
ai_decision_log read / count / delete is keyed by THIS script's fixture org ids
only (real AI calls write the log at any time). Check 3 runs on a fixture org
with no ai.model.* rows instead of the real default org (never write a real
row); its assertions are unchanged. This script has no users / roles / audit_log
seeding. It always ends in ``TOTAL: N PASS, M FAIL`` — an exception anywhere is
a ``[FAIL]`` line, then the TOTAL, never a bare traceback.
"""
import os as _authz_os
_authz_os.environ["AUTHZ_TEST_MODE"] = "1"  # AUTHZ1: in-process test identity only; set before main is imported

import asyncio
import json
import os
import subprocess
import sys
import traceback
import types
import uuid

import asyncpg

API_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if API_DIR not in sys.path:
    sys.path.insert(0, API_DIR)
REPO_ROOT = os.path.dirname(os.path.dirname(API_DIR))
SCRIPTS_DIR = os.path.join(API_DIR, "scripts")
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

from _db_connect import hydrate, resolve_dsn  # noqa: E402

hydrate()  # Doppler over HTTPS, BEFORE services.* is imported (Settings is lru_cached)
# s28 fix 1: the platform default is the LiteLLM deployment name since litellmseedfix (201065f), which renamed
# DEFAULT_SETTINGS' ai.model.default / fallback_chain from 'claude-haiku-4-5-20251001' and updated
# verify_litellmphased2 but not this script (hidden until now behind the organizations-insert RLS crash).
HAIKU = "claude-haiku"

PASS = "[PASS]"
FAIL = "[FAIL]"

results = []


def record(label, ok, note=""):
    results.append((label, ok, note))
    icon = PASS if ok else FAIL
    print(f"{icon} {label}{f'  ({note})' if note else ''}")


async def platform(conn, sql, *args, fetch="execute"):
    """ONE transaction with the Super Admin RLS carve-out SET LOCAL (as verify_s28's platform())."""
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        return await getattr(conn, fetch)(sql, *args)


async def in_org(org, coro_fn):
    """Run an AI call / resolver under the org's own request context (what rls_context_middleware sets)."""
    from services.database import reset_rls_context, set_rls_context
    tokens = set_rls_context(org, False)
    try:
        return await coro_fn()
    finally:
        reset_rls_context(tokens)


# ── Fake Anthropic seam ────────────────────────────────────────────────────
# _execute_chain does `import anthropic as _anthropic;
# client = _anthropic.AsyncAnthropic(api_key=...)`; make_call() then awaits
# `client.messages.create(model=..., ...)`. We replace the whole module so no
# real SDK / key / network is needed and failures are deterministic per model.

class _FakeUsage:
    def __init__(self, i, o):
        self.input_tokens = i
        self.output_tokens = o


class _FakeBlock:
    def __init__(self, text):
        self.text = text
        self.type = "text"

    def model_dump(self):
        return {"type": "text", "text": self.text}


class _FakeMessage:
    def __init__(self, text, usage, stop_reason="end_turn"):
        self.content = [_FakeBlock(text)]
        self.usage = usage
        self.stop_reason = stop_reason


class _FakeMessages:
    async def create(self, *, model, max_tokens=None, system=None,
                     messages=None, tools=None, **_):
        # Deterministic: a model whose id contains BROKEN is "down".
        await asyncio.sleep(0.002)  # a real, measurable latency (>0 ms)
        if "BROKEN" in (model or ""):
            raise RuntimeError(f"simulated outage for model {model!r}")
        return _FakeMessage('{"result": "ok"}', _FakeUsage(120, 80))


class _FakeAsyncAnthropic:
    def __init__(self, *a, **k):
        self.messages = _FakeMessages()


def _install_fake_anthropic():
    mod = types.ModuleType("anthropic")
    mod.AsyncAnthropic = _FakeAsyncAnthropic
    sys.modules["anthropic"] = mod


# ── Bypass sweep (Task 1 finding, asserted) ─────────────────────────────────
def sweep_bypasses():
    """Return the set of files (outside scripts/) that call .messages.create."""
    cmd = [
        "grep", "-rlIE", r"\.messages\.create", "apps/",
        "--include=*.py",
    ]
    proc = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True)
    files = []
    for line in proc.stdout.splitlines():
        if "node_modules" in line or "/scripts/" in line or "/venv/" in line:
            continue
        files.append(line.strip())
    return files


async def main():
    dsn, how = await resolve_dsn("DATABASE_URL")
    if not dsn:
        record("connect: a working DATABASE_URL", False, how)
        return
    os.environ["DATABASE_URL"] = dsn  # the app pool (services.database) reads the same working DSN

    # Hermetic AI: fake seam + a dummy key so the no-key early-return is skipped.
    _install_fake_anthropic()
    os.environ["ANTHROPIC_API_KEY"] = "sk-verify-s27-hermetic"

    import services.extraction as extraction  # noqa: E402
    from services.database import close_pool  # noqa: E402
    from services.extraction import (  # noqa: E402
        FALLBACK_CHAIN_KEY,
        call_claude_json,
        call_claude_text,
        resolve_fallback_chain,
        resolve_model,
    )
    from services.org_settings import DEFAULT_SETTINGS  # noqa: E402

    conn = await asyncpg.connect(dsn, statement_cache_size=0)

    org_a = str(uuid.uuid4())  # fall-through org
    org_b = str(uuid.uuid4())  # different per-org chain
    org_c = str(uuid.uuid4())  # no ai.model.* → DEFAULT_SETTINGS behaviour
    org_d = str(uuid.uuid4())  # check 3 (success path) — no ai.model.*, never the real default org
    fresh = (org_a, org_b, org_c, org_d)

    A_PRIMARY = "claude-s27a-primary-BROKEN"
    A_BACKUP = "claude-s27a-backup"
    B_PRIMARY = "claude-s27b-primary-BROKEN"
    B_BACKUP = "claude-s27b-backup"

    async def teardown():
        # Only OUR rows: this run's fixture org ids — never a real org's rows.
        await platform(
            conn, "DELETE FROM ai_decision_log WHERE org_id = ANY($1::uuid[])",
            list(fresh),
        )
        await platform(
            conn, "DELETE FROM org_settings WHERE org_id = ANY($1::uuid[])", list(fresh)
        )
        await platform(
            conn, "DELETE FROM organizations WHERE id = ANY($1::uuid[]) AND is_test",
            list(fresh),
        )

    async def seed_setting(org, key, value):
        await platform(
            conn,
            "INSERT INTO org_settings "
            "  (org_id, setting_key, setting_value, category, is_public) "
            "VALUES ($1, $2, $3::jsonb, 'ai', false) "
            "ON CONFLICT (org_id, setting_key) DO UPDATE "
            "  SET setting_value = EXCLUDED.setting_value, updated_at = now()",
            org, key, json.dumps(value),
        )

    async def latest(org, task_type):
        return await platform(
            conn,
            "SELECT * FROM ai_decision_log "
            "WHERE org_id = $1 AND task_type = $2 "
            "ORDER BY created_at DESC LIMIT 1",
            org, task_type, fetch="fetchrow",
        )

    try:
        await teardown()  # teardown-at-start
        for org, name in ((org_a, "A"), (org_b, "B"), (org_c, "C"), (org_d, "D")):
            await platform(
                conn,
                "INSERT INTO organizations (id, name, slug, is_test) VALUES ($1, $2, $3, true) "
                "ON CONFLICT (id) DO NOTHING",
                org, f"Verify S27 Org {name}", f"verify-s27-{org[:8]}",
            )
        await seed_setting(org_a, "ai.model.default", A_PRIMARY)
        await seed_setting(org_a, FALLBACK_CHAIN_KEY, [A_BACKUP])
        await seed_setting(org_b, "ai.model.default", B_PRIMARY)
        await seed_setting(org_b, FALLBACK_CHAIN_KEY, [B_BACKUP])
        # org_c gets NO ai.model.* rows on purpose.

        # ── 1. ai_decision_log matches the snapshot ─────────────────────────
        cols = {
            r["column_name"]: r["data_type"]
            for r in await conn.fetch(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_name = 'ai_decision_log' AND table_schema = 'public'"
            )
        }
        expected = {
            "id": "uuid", "org_id": "uuid", "task_type": "text",
            "model_requested": "text", "model_used": "text",
            "fallback_used": "boolean", "fallback_reason": "text",
            "cost_usd": "numeric", "latency_ms": "integer",
            "success": "boolean", "error_detail": "text",
            "created_at": "timestamp with time zone",
        }
        mismatch = {k: (v, cols.get(k)) for k, v in expected.items()
                    if cols.get(k) != v}
        record("1. ai_decision_log exists matching the snapshot", not mismatch,
               "ok" if not mismatch else f"mismatch {mismatch}")

        # ── 2. Task 1 discovery: central fn + zero bypasses ─────────────────
        bypass_files = sweep_bypasses()
        only_central = bypass_files == ["apps/api/services/extraction.py"]
        print("      Task 1: central mechanism = services/extraction.py "
              "(call_claude_json / call_claude_text / call_claude_with_tools, "
              "model via resolve_model + resolve_fallback_chain).")
        print(f"      .messages.create found only in: {bypass_files}")
        record("2. Task 1 findings — zero bypasses (only central helper)",
               only_central,
               "0 bypasses" if only_central else f"BYPASS: {bypass_files}")

        # ── 3. Successful call → correct row, real cost/latency ─────────────
        out = await in_org(org_d, lambda: call_claude_json(
            "sys", "hi", org_id=org_d, task_type="verify_s27_success"
        ))
        row = await latest(org_d, "verify_s27_success")
        ok3 = (
            out == {"result": "ok"}
            and row is not None
            and row["model_requested"] == HAIKU
            and row["model_used"] == HAIKU
            and row["model_requested"] == row["model_used"]
            and row["fallback_used"] is False
            and row["success"] is True
            and row["fallback_reason"] is None
            and row["cost_usd"] is not None and row["cost_usd"] > 0
            and row["latency_ms"] is not None and row["latency_ms"] > 0
        )
        record(
            "3. successful call writes correct row (req==used, no fallback, "
            "real cost+latency)",
            ok3,
            "" if row is None else
            f"used={row['model_used']} fb={row['fallback_used']} "
            f"cost={row['cost_usd']} lat={row['latency_ms']}ms",
        )

        # ── 4. Primary failure → chain falls through to next model ──────────
        out4 = await in_org(org_a, lambda: call_claude_json(
            "sys", "hi", org_id=org_a, task_type="verify_s27_fallthrough"
        ))
        row4 = await latest(org_a, "verify_s27_fallthrough")
        ok4 = (
            out4 == {"result": "ok"}
            and row4 is not None
            and row4["model_requested"] == A_PRIMARY
            and row4["model_used"] == A_BACKUP
            and row4["model_requested"] != row4["model_used"]
            and row4["fallback_used"] is True
            and row4["success"] is True
            and bool(row4["fallback_reason"])
        )
        record(
            "4. primary failure falls through chain (req!=used, fallback_used, "
            "reason set)",
            ok4,
            "" if row4 is None else
            f"req={row4['model_requested']} used={row4['model_used']} "
            f"reason={row4['fallback_reason']!r}",
        )

        # ── 5. Different org uses ITS OWN chain, not another org's ──────────
        out5 = await in_org(org_b, lambda: call_claude_json(
            "sys", "hi", org_id=org_b, task_type="verify_s27_perorg"
        ))
        row5 = await latest(org_b, "verify_s27_perorg")
        ok5 = (
            out5 == {"result": "ok"}
            and row5 is not None
            and row5["model_requested"] == B_PRIMARY
            and row5["model_used"] == B_BACKUP        # org B's own backup
            and row5["model_used"] != A_BACKUP        # NOT org A's backup
            and row5["fallback_used"] is True
        )
        record(
            "5. different org uses its OWN fallback_chain (per-org, not shared)",
            ok5,
            "" if row5 is None else
            f"used={row5['model_used']} (A's backup was {A_BACKUP})",
        )

        # ── 6. No explicit chain → DEFAULT_SETTINGS behaviour ───────────────
        chain_c = await in_org(org_c, lambda: resolve_fallback_chain(org_c))
        model_c = await in_org(org_c, lambda: resolve_model(org_c))
        out6 = await in_org(org_c, lambda: call_claude_json(
            "sys", "hi", org_id=org_c, task_type="verify_s27_defaults"
        ))
        row6 = await latest(org_c, "verify_s27_defaults")
        ok6 = (
            chain_c == DEFAULT_SETTINGS[FALLBACK_CHAIN_KEY] == [HAIKU]
            and model_c == HAIKU
            and out6 == {"result": "ok"}
            and row6 is not None
            and row6["model_used"] == HAIKU
            and row6["fallback_used"] is False
        )
        record(
            "6. org with no chain falls back to DEFAULT_SETTINGS (mini-bedrock "
            "behaviour preserved)",
            ok6,
            f"chain={chain_c} model={model_c}",
        )

        # ── 7. Logging failure is non-blocking ──────────────────────────────
        original = extraction._write_ai_decision

        async def _boom(**_):
            raise RuntimeError("simulated ai_decision_log write failure")

        extraction._write_ai_decision = _boom
        try:
            out7 = await in_org(org_c, lambda: call_claude_text(
                "sys", [{"role": "user", "content": "hi"}],
                org_id=org_c, task_type="verify_s27_nonblocking",
            ))
        finally:
            extraction._write_ai_decision = original
        # The AI result must come back despite the broken log; and because the
        # write truly failed, no row should exist for this task_type.
        n_nb = await platform(
            conn,
            "SELECT count(*) FROM ai_decision_log "
            "WHERE org_id = ANY($1::uuid[]) AND task_type = 'verify_s27_nonblocking'",
            list(fresh), fetch="fetchval",
        )
        ok7 = out7 == '{"result": "ok"}' and n_nb == 0
        record(
            "7. logging failure does NOT block the AI result (non-blocking)",
            ok7, f"result={out7!r} rows_written={n_nb}",
        )

        # ── 8. Teardown leaves zero rows ────────────────────────────────────
        await teardown()
        leftover_log = await platform(
            conn, "SELECT count(*) FROM ai_decision_log WHERE org_id = ANY($1::uuid[])",
            list(fresh), fetch="fetchval",
        )
        leftover_settings = await platform(
            conn, "SELECT count(*) FROM org_settings WHERE org_id = ANY($1::uuid[])",
            list(fresh), fetch="fetchval",
        )
        leftover_orgs = await platform(
            conn, "SELECT count(*) FROM organizations WHERE id = ANY($1::uuid[])",
            list(fresh), fetch="fetchval",
        )
        ok8 = leftover_log == 0 and leftover_settings == 0 and leftover_orgs == 0
        record(
            "8. teardown — zero leftover rows (count(*) confirmed)",
            ok8,
            f"log={leftover_log} settings={leftover_settings} orgs={leftover_orgs}",
        )

    except Exception as exc:  # noqa: BLE001 — a crash is a FAIL line, never a bare traceback
        traceback.print_exc()
        record("verify_s27 ran to completion (seeding + checks raised)", False,
               f"{type(exc).__name__}: {exc}")
    finally:
        try:
            await teardown()
        except Exception as exc:  # noqa: BLE001
            record("teardown (finally) raised", False, f"{type(exc).__name__}: {exc}")
        await conn.close()
        try:
            await close_pool()
        except Exception:
            pass


def _total_and_exit():
    n_pass = sum(1 for _, ok, _ in results if ok)
    n_fail = sum(1 for _, ok, _ in results if not ok)
    if n_pass + n_fail == 0:
        print("[FAIL] verify_s27 recorded no checks")
        n_fail = 1
    print(f"\nTOTAL: {n_pass} PASS, {n_fail} FAIL")
    sys.exit(0 if n_fail == 0 else 1)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except BaseException as exc:  # noqa: BLE001 — still print the TOTAL
        if isinstance(exc, SystemExit):
            raise
        traceback.print_exc()
        record("verify_s27 main() raised", False, f"{type(exc).__name__}: {exc}")
    _total_and_exit()
