"""verify_minibedrock.py — config-driven AI model resolution.

Which model the platform calls is now a CONFIG value (org_settings, category
'ai'), resolved per-org via services/extraction.resolve_model, NOT a hardcoded
string scattered through the codebase. Switching a client — or the whole
platform (e.g. a future AWS Bedrock move) — to another model becomes a settings
change, not a code change.

Column names are taken directly from docs/schema_snapshot.sql:

  org_settings: id, org_id, setting_key, setting_value (jsonb NOT NULL),
    category, is_public, updated_at, updated_by, created_at
    UNIQUE org_settings_org_id_setting_key_key: (org_id, setting_key)
    NOT bi-temporal — plain upsert, no valid_from / valid_to.
  organizations: id, name, slug (UNIQUE), created_at

The EXACT model strings discovered in Task 1 (preserving prior behaviour):
  ai.model.default   = claude-haiku-4-5-20251001
  ai.model.provider  = anthropic
  ai.model.fallback  = claude-haiku-4-5-20251001  (no prior fallback pattern)
  ai.model.assistant = claude-sonnet-4-6          (the second live model)

Assertions:
  1. ai.model.default/provider/fallback (+ assistant) exist in org_settings for
     the default org with the EXACT Task-1 values.
  2. The same keys exist in DEFAULT_SETTINGS with matching values.
  3. get_setting(default_org, 'ai.model.default') returns the correct value.
  4. A fresh org with NO explicit ai.model.* settings falls back to
     DEFAULT_SETTINGS — via both get_setting AND the central resolve_model.
  5. THE SWEEP — zero hardcoded model strings outside DEFAULT_SETTINGS and the
     seed/migration; every call site resolves through org_settings.
  6. Teardown: zero leftover rows for the fresh org, confirmed via count(*).

Idempotent, teardown-at-start and teardown-at-end, no interactive prompts.
"""
import asyncio
import os
import subprocess
import sys
import uuid

import asyncpg

API_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if API_DIR not in sys.path:
    sys.path.insert(0, API_DIR)

REPO_ROOT = os.path.dirname(os.path.dirname(API_DIR))

from services.extraction import (  # noqa: E402
    ASSISTANT_MODEL_KEY,
    DEFAULT_MODEL_KEY,
    resolve_model,
)
from services.org_settings import DEFAULT_SETTINGS, get_setting  # noqa: E402

DATABASE_URL = os.environ.get("DATABASE_URL")

DEFAULT_ORG = "00000000-0000-0000-0000-000000000001"

# The exact values discovered in Task 1 — the expected ground truth.
EXPECTED = {
    "ai.model.default": "claude-haiku-4-5-20251001",
    "ai.model.provider": "anthropic",
    "ai.model.fallback": "claude-haiku-4-5-20251001",
    "ai.model.assistant": "claude-sonnet-4-6",
}
REQUIRED_KEYS = ("ai.model.default", "ai.model.provider", "ai.model.fallback")

PASS = "\033[32m[Y]\033[0m"
FAIL = "\033[31m[N]\033[0m"

results = []


def record(label, ok, note=""):
    results.append((label, ok, note))
    icon = PASS if ok else FAIL
    suffix = f"  ({note})" if note else ""
    print(f"  {icon} {label}{suffix}")


# ── The sweep gate ─────────────────────────────────────────────────────────
# Scope: application source that actually calls the model. A literal Anthropic
# model id may live in ONLY two places: DEFAULT_SETTINGS (the fallback data) and
# the seed SQL (the seed data). Every other call site must resolve through
# org_settings now. docs/ is the seed's home and is not app code, so the sweep
# stays on apps/ + scripts/ code files.
MODEL_RE = r"claude-(haiku|sonnet|opus|fable)"

SWEEP_INCLUDES = [
    "--include=*.py", "--include=*.js", "--include=*.jsx",
    "--include=*.ts", "--include=*.tsx", "--include=*.mjs",
]

# The only files permitted to contain a literal model string.
ALLOWED = (
    # 1. DEFAULT_SETTINGS — this IS the fallback data, not call-site logic.
    "apps/api/services/org_settings.py",
    # 2. This verify script — it contains the patterns/values it greps for.
    "apps/api/scripts/verify_minibedrock.py",
)


def _grep(pattern):
    cmd = ["grep", "-rInE", pattern, "apps/", "scripts/", *SWEEP_INCLUDES]
    proc = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True)
    lines = []
    for line in proc.stdout.splitlines():
        if "node_modules" in line or "/.next/" in line or "/venv/" in line:
            continue
        lines.append(line)
    return lines


def run_sweep():
    """Return (violations, allowed_hits)."""
    violations, allowed_hits = [], []
    for line in _grep(MODEL_RE):
        path = line.split(":", 1)[0]
        if path in ALLOWED:
            allowed_hits.append(line)
        else:
            violations.append(line)
    return violations, allowed_hits


async def main():
    if not DATABASE_URL:
        print("[N] SKIP — DATABASE_URL not set")
        return

    conn = await asyncpg.connect(DATABASE_URL, statement_cache_size=0)

    fresh_org = str(uuid.uuid4())  # a tenant with NO explicit ai.model.* rows

    teardown_failed = False

    async def teardown():
        nonlocal teardown_failed
        try:
            await conn.execute(
                "DELETE FROM org_settings WHERE org_id = $1", fresh_org
            )
            await conn.execute(
                "DELETE FROM organizations WHERE id = $1", fresh_org
            )
        except Exception as exc:
            teardown_failed = True
            print(f"  [teardown error] {exc}", file=sys.stderr)

    # Teardown-at-start: a previous crashed run must not colour this one.
    await teardown()
    teardown_failed = False

    try:
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3) "
            "ON CONFLICT (id) DO NOTHING",
            fresh_org, "Verify Bedrock Tenant", f"verify-bedrock-{fresh_org[:8]}",
        )

        # ── 1. Keys exist in org_settings for the default org, exact values ──
        rows = await conn.fetch(
            "SELECT setting_key, setting_value, category FROM org_settings "
            "WHERE org_id = $1 AND setting_key = ANY($2::text[])",
            DEFAULT_ORG, list(EXPECTED),
        )
        import json as _json
        stored = {r["setting_key"]: _json.loads(r["setting_value"]) for r in rows}
        cats = {r["setting_key"]: r["category"] for r in rows}
        missing = [k for k in REQUIRED_KEYS if k not in stored]
        wrong = {k: stored.get(k) for k in EXPECTED
                 if k in stored and stored[k] != EXPECTED[k]}
        bad_cat = [k for k in EXPECTED if k in cats and cats[k] != "ai"]
        ok1 = not missing and not wrong and not bad_cat and "ai.model.assistant" in stored
        note1 = ""
        if missing:
            note1 = f"missing {missing}"
        elif wrong:
            note1 = f"wrong values {wrong}"
        elif bad_cat:
            note1 = f"category not 'ai': {bad_cat}"
        elif "ai.model.assistant" not in stored:
            note1 = "assistant key missing"
        record(
            "1. ai.model.* seeded on default org with exact Task-1 values",
            ok1, note1,
        )

        # ── 2. Same keys in DEFAULT_SETTINGS with matching values ────────────
        ds_missing = [k for k in EXPECTED if k not in DEFAULT_SETTINGS]
        ds_wrong = {k: DEFAULT_SETTINGS.get(k) for k in EXPECTED
                    if k in DEFAULT_SETTINGS and DEFAULT_SETTINGS[k] != EXPECTED[k]}
        ok2 = not ds_missing and not ds_wrong
        record(
            "2. keys present in DEFAULT_SETTINGS with matching values",
            ok2, (f"missing {ds_missing}" if ds_missing
                  else f"wrong {ds_wrong}" if ds_wrong else ""),
        )

        # ── 3. get_setting resolves the default org's stored value ───────────
        got = await get_setting(conn, DEFAULT_ORG, "ai.model.default")
        ok3 = got == EXPECTED["ai.model.default"]
        record("3. get_setting(default_org, 'ai.model.default') correct",
                ok3, f"got {got!r}")

        # ── 4. Fresh org with no override falls back to DEFAULT_SETTINGS ──────
        # 4a: get_setting sees no row and returns the DEFAULT_SETTINGS value.
        fb_get = await get_setting(conn, fresh_org, "ai.model.default")
        # 4b: the central resolver used by every call site does the same.
        fb_default = await resolve_model(fresh_org, key=DEFAULT_MODEL_KEY)
        fb_assistant = await resolve_model(fresh_org, key=ASSISTANT_MODEL_KEY)
        # 4c: no org context at all still resolves the platform default.
        fb_none = await resolve_model(None)
        ok4 = (
            fb_get == EXPECTED["ai.model.default"]
            and fb_default == EXPECTED["ai.model.default"]
            and fb_assistant == EXPECTED["ai.model.assistant"]
            and fb_none == EXPECTED["ai.model.default"]
        )
        record(
            "4. fresh org (no ai.model.*) falls back via get_setting + resolve_model",
            ok4,
            f"get={fb_get!r} default={fb_default!r} assistant={fb_assistant!r} none={fb_none!r}",
        )

        # ── 5. The sweep: zero hardcoded model strings at call sites ─────────
        violations, allowed_hits = run_sweep()
        ok5 = not violations
        note5 = (f"{len(allowed_hits)} allowed hits, 0 violations" if ok5
                 else f"{len(violations)} violation(s): "
                      + "; ".join(violations[:3]))
        record("5. sweep — zero hardcoded model strings outside allowed files",
               ok5, note5)

        # ── 6. Teardown leaves zero rows for the fresh org ───────────────────
        await teardown()
        n_settings = await conn.fetchval(
            "SELECT count(*) FROM org_settings WHERE org_id = $1", fresh_org
        )
        n_orgs = await conn.fetchval(
            "SELECT count(*) FROM organizations WHERE id = $1", fresh_org
        )
        ok6 = n_settings == 0 and n_orgs == 0 and not teardown_failed
        record("6. teardown — zero leftover rows",
               ok6, f"settings={n_settings} orgs={n_orgs}")

    finally:
        await teardown()
        await conn.close()

    total = len(results)
    passed = sum(1 for _, ok, _ in results if ok)
    print(f"\n{passed}/{total} passed")
    sys.exit(0 if passed == total else 1)


if __name__ == "__main__":
    asyncio.run(main())
