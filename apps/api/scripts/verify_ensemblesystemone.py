"""verify_ensemblesystemone.py — ensemble = two LLMs + one System One model.

WHAT WAS BUILT (ensemblesystemone.structural; this script proves it):
  * migrations/ensemblesystemone_reshape.sql — ai_ensemble_configs columns
    renamed to model_1 / model_2 / system_one_model (+ *_version), and
    comparison_kind dropped; ai_judgment_models renamed to
    ai_system_one_models with model_route, is_default (partial unique index
    ai_system_one_models_one_default), and last_check_detail. An FK ties
    system_one_model to the System One catalog.
  * services/system_one.py — the System One catalog, plus
    verify_system_one_model: the ONLY path to 'available'. It runs a real
    models listing and one real decision through the LiteLLM proxy's /typesafe
    generic pass-through.
  * services/ai_ensembles.py — the picker (LLM options from
    platform_model_catalog + Phase E's chat_capable_models; System One options
    preselected to the default) and activate_ensemble (validate, retire and
    insert in ONE transaction, with version snapshots).
  * routers/ai_ensembles.py — /admin/ai/ensembles and /admin/system-one-catalog.
  * scripts/configure_typesafe_passthrough.py — the proxy route (already
    applied live by the sprint).

REMOVED by this sprint: services/ai_model_catalog.py (a parallel LLM
catalog unioning /model/info), GET /admin/ai/model-catalog,
scripts/verify_ensemblemodels.py, and every comparison_kind / LLM-comparator
code path.

Hydrates secrets from Doppler over HTTPS at startup (the verify_tiergating.py
pattern). Never prints a credential value. TYPESAFE_API_KEY is read into
memory from Doppler prd_lite_llm ONLY to prove it appears nowhere in app code,
app env, or app requests. It is never printed and is deleted after use.

RLS context is set on EVERY read and write of an RLS table (``_super``
inside an explicit transaction), including before/after comparisons.

ASSERTIONS:
  [Y] Task 1's four findings reported
  [Y] comparison_kind gone, columns renamed, no LLM-comparator code remains
  [Y] A second System One default is refused by the database
  [Y] super_admin edits the System One catalog; org_admin gets 403 on the
      identical request
  [Y] Model 1/2 refuse voyage-3.5, refuse unavailable models, refuse M1 == M2
  [Y] The System One slot refuses an LLM and refuses a disabled entry
  [Y] The picker preselects the default System One model
  [Y] Activation snapshots versions and retires the previous config in one
      transaction; the immutability trigger blocks edits
  [Y] Jev: real call through the proxy -> 'available', or BLOCKED if the key
      is absent (never faked, never FAIL)
  [Y] The TypeSafe key never appears in app code or app env; the request
      carries only a LiteLLM key
  [Y] Four RLS policies on each table, re-verified after the DDL
  [Y] Teardown: zero leftover rows; real catalog rows and real default untouched

Pass/fail only. Prints 'TOTAL: N PASS, M FAIL' and exits non-zero on failure.

Run:  python3 apps/api/scripts/verify_ensemblesystemone.py
"""
from __future__ import annotations

import asyncio
import json
import math
import os
import pathlib
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from uuid import UUID

HERE = pathlib.Path(__file__).resolve()
sys.path.insert(0, str(HERE.parent))
from _db_bootstrap import bootstrap_async  # noqa: E402  (also puts apps/api on sys.path)
from _doppler_env import _token_from_cli_config  # noqa: E402

import asyncpg  # noqa: E402

REPO = HERE.parents[3]
API_DIR = HERE.parents[1]

# ═══════════════════════════════════════════════════════════════════════════
# Fixtures — the "e51" block is unused by any other verify script (grep'd).
# Every unique value below uses the FULL UUID, never a slice of one.
# ═══════════════════════════════════════════════════════════════════════════
ORG = UUID("99000000-0000-0000-0000-0000e5100001")
U_SUPER = UUID("99000000-0000-0000-0000-0000e5100011")
U_ORGADMIN = UUID("99000000-0000-0000-0000-0000e5100012")
FIXTURE_USERS = [U_SUPER, U_ORGADMIN]

S1_AVAILABLE = f"ensv-s1-available-{UUID('99000000-0000-0000-0000-0000e5100021')}"
S1_DISABLED = f"ensv-s1-disabled-{UUID('99000000-0000-0000-0000-0000e5100022')}"
S1_SECOND_DEFAULT = f"ensv-s1-second-default-{UUID('99000000-0000-0000-0000-0000e5100023')}"
S1_HTTP = f"ensv-s1-http-{UUID('99000000-0000-0000-0000-0000e5100024')}"
S1_FIXTURE_VERSION = f"ensv-version-{UUID('99000000-0000-0000-0000-0000e5100025')}"
LLM_DISABLED = f"ensv-llm-disabled-{UUID('99000000-0000-0000-0000-0000e5100031')}"
FIXTURE_TASK = f"ensv_task_{UUID('99000000-0000-0000-0000-0000e5100041').hex}"
FIXTURE_PREFIX = "ensv-"

REAL_TASK = "note_terms_hazard"
JEV_KEY = "typesafe-jev"
JEV_ROUTE = "jev-1.13.0"
# Columns the Jev live check is ALLOWED to change on the real Jev row. Every
# other column of every real row must be byte-identical before and after.
JEV_CHECK_COLUMNS = {"availability", "last_verified_at", "model_version", "last_check_detail"}

HEADERS = {"Authorization": "Bearer verify-token"}

_ok = True
_n_pass = 0
_n_fail = 0
_finds: list[str] = []
_blocked: list[str] = []


def check(passed: bool, label: str, detail: str = "") -> bool:
    assert isinstance(passed, bool), (
        f"check() received passed={passed!r} (type {type(passed).__name__}), "
        f"not a bool, for label={label!r} — reversed-argument guard"
    )
    global _ok, _n_pass, _n_fail
    line = f"{'[PASS]' if passed else '[FAIL]'} {label}"
    if detail:
        line += f"  — {detail}"
    print(line)
    if passed:
        _n_pass += 1
    else:
        _n_fail += 1
        _ok = False
    return passed


def find(label: str, detail: str = "") -> None:
    print(f"[FIND] {label}" + (f"  — {detail}" if detail else ""))
    _finds.append(label)


def blocked(label: str, detail: str = "") -> None:
    print(f"[BLOCKED] {label}" + (f"  — {detail}" if detail else ""))
    _blocked.append(label)


def section(title: str) -> None:
    print(f"\n── {title} ──")


# ═══════════════════════════════════════════════════════════════════════════
# Doppler (names, and one value held in memory only) + LiteLLM HTTP
# ═══════════════════════════════════════════════════════════════════════════
def _doppler_token() -> str:
    tok = (os.environ.get("DOPPLER_TOKEN") or "").strip()
    if not tok:
        tok, _, _ = _token_from_cli_config()
    return tok


def doppler_names(config: str) -> set[str] | None:
    url = "https://api.doppler.com/v3/configs/config/secrets/names?" + urllib.parse.urlencode(
        {"project": "hollisworks", "config": config}
    )
    req = urllib.request.Request(
        url, headers={"Authorization": f"Bearer {_doppler_token()}", "Accept": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return set(json.loads(r.read()).get("names", []))
    except Exception:  # noqa: BLE001 — the body can echo the token; report nothing
        return None


def doppler_secret_value(config: str, name: str) -> str | None:
    """Held in memory for a comparison only. NEVER printed."""
    url = "https://api.doppler.com/v3/configs/config/secret?" + urllib.parse.urlencode(
        {"project": "hollisworks", "config": config, "name": name}
    )
    req = urllib.request.Request(
        url, headers={"Authorization": f"Bearer {_doppler_token()}", "Accept": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            payload = json.loads(r.read())
    except Exception:  # noqa: BLE001
        return None
    value = payload.get("value") if isinstance(payload, dict) else None
    value = (value.get("computed") or value.get("raw")) if isinstance(value, dict) else None
    return value or None


def litellm(path: str, *, method: str = "GET", body: dict | None = None,
            auth: bool = True) -> tuple[int, str]:
    base = os.environ["LITELLM_BASE_URL"].rstrip("/")
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{base}{path}", data=data, method=method)
    if auth:
        req.add_header("Authorization", f"Bearer {os.environ['LITELLM_MASTER_KEY']}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        return 0, type(e).__name__


# ═══════════════════════════════════════════════════════════════════════════
# DB helpers — every RLS read/write sets super-admin context in its own txn
# ═══════════════════════════════════════════════════════════════════════════
async def _super(conn) -> None:
    await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")


async def rls_fetch(conn, q, *a):
    async with conn.transaction():
        await _super(conn)
        return await conn.fetch(q, *a)


async def rls_row(conn, q, *a):
    async with conn.transaction():
        await _super(conn)
        return await conn.fetchrow(q, *a)


async def rls_val(conn, q, *a):
    async with conn.transaction():
        await _super(conn)
        return await conn.fetchval(q, *a)


async def rls_expect_error(conn, q, *a) -> Exception | None:
    """Run one statement under super-admin context; return the exception it
    raised (or None). The transaction is always rolled back."""
    try:
        async with conn.transaction():
            await _super(conn)
            await conn.execute(q, *a)
            raise _Rollback()
    except _Rollback:
        return None
    except Exception as exc:  # noqa: BLE001
        return exc


class _Rollback(Exception):
    pass


def _norm(row) -> dict:
    return {k: (str(v) if v is not None else None) for k, v in dict(row).items()}


async def snapshot_real(conn) -> dict:
    llm = await rls_fetch(conn, "SELECT * FROM platform_model_catalog WHERE model_id NOT LIKE $1 ORDER BY model_id", FIXTURE_PREFIX + "%")
    s1 = await rls_fetch(conn, "SELECT * FROM ai_system_one_models WHERE key NOT LIKE $1 ORDER BY key", FIXTURE_PREFIX + "%")
    cfg = await rls_fetch(conn, "SELECT * FROM ai_ensemble_configs WHERE task_key <> $1 ORDER BY id", FIXTURE_TASK)
    default = await rls_val(conn, "SELECT key FROM ai_system_one_models WHERE is_default")
    return {
        "llm": [_norm(r) for r in llm],
        "s1": [_norm(r) for r in s1],
        "cfg": [_norm(r) for r in cfg],
        "default": default,
    }


async def teardown(conn) -> None:
    fixture_subs = [_sub(u) for u in FIXTURE_USERS]
    async with conn.transaction():
        await _super(conn)
        # Configs first (the immutability trigger blocks UPDATE, not DELETE;
        # the FK from system_one_model means fixture S1 rows go after).
        await conn.execute("DELETE FROM ai_ensemble_configs WHERE task_key = $1", FIXTURE_TASK)
        await conn.execute(
            "DELETE FROM ai_ensemble_configs WHERE system_one_model LIKE $1", FIXTURE_PREFIX + "%"
        )
        await conn.execute("DELETE FROM ai_system_one_models WHERE key LIKE $1", FIXTURE_PREFIX + "%")
        await conn.execute("DELETE FROM org_model_selections WHERE model_id LIKE $1", FIXTURE_PREFIX + "%")
        await conn.execute("DELETE FROM platform_model_catalog WHERE model_id LIKE $1", FIXTURE_PREFIX + "%")
        # BEFORE deleting fixture users: everything that may reference them.
        await conn.execute(
            "DELETE FROM audit_log WHERE org_id = $1 OR user_id = ANY($2::uuid[])", ORG, FIXTURE_USERS
        )
        await conn.execute(
            "DELETE FROM assistant_activities WHERE org_id = $1 OR user_id = ANY($2::uuid[])",
            ORG, FIXTURE_USERS,
        )
        await conn.execute(
            "DELETE FROM agent_proposals WHERE org_id = $1 OR proposed_by = ANY($2::uuid[])",
            ORG, FIXTURE_USERS,
        )
        await conn.execute(
            "DELETE FROM user_roles WHERE user_id = ANY($1::uuid[]) "
            "OR role_id IN (SELECT id FROM roles WHERE org_id = $2)",
            FIXTURE_USERS, ORG,
        )
        await conn.execute(
            "DELETE FROM role_permissions WHERE role_id IN (SELECT id FROM roles WHERE org_id = $1)", ORG
        )
        await conn.execute("DELETE FROM roles WHERE org_id = $1", ORG)
        await conn.execute(
            "DELETE FROM users WHERE id = ANY($1::uuid[]) OR auth0_sub = ANY($2::text[])",
            FIXTURE_USERS, fixture_subs,
        )
        await conn.execute("DELETE FROM organizations WHERE id = $1", ORG)


def _sub(uid: UUID) -> str:
    return f"ensemblesystemone_{uid.hex}"


async def seed(conn) -> None:
    from services.rbac import grant_org_admin

    async with conn.transaction():
        await _super(conn)
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3)",
            ORG, "ENSEMBLESYSTEMONE Fixture Org", f"{ORG}-ensemblesystemone",
        )
        for uid, role in ((U_SUPER, "super_admin"), (U_ORGADMIN, "org_admin")):
            sub = _sub(uid)
            await conn.execute(
                """INSERT INTO users (id, org_id, email, full_name, auth0_sub, role, is_active)
                   VALUES ($1, $2, $3, $4, $5, $6, true)""",
                uid, ORG, f"{sub}@test.local", sub, sub, role,
            )
        await grant_org_admin(conn, U_ORGADMIN, ORG)

        # Fixture System One entries. These bypass Verify now on purpose:
        # they exist to exercise the picker and activation, not Jev. The DB
        # CHECK still applies ('available' needs last_verified_at + version).
        await conn.execute(
            """INSERT INTO ai_system_one_models
                 (key, display_name, provider, model_route, model_version,
                  availability, is_default, last_verified_at, last_check_detail, notes)
               VALUES ($1, 'ENSV fixture (available)', 'typesafe', 'fixture-route', $2,
                       'available', false, now(), 'fixture', 'verify_ensemblesystemone fixture'),
                      ($3, 'ENSV fixture (disabled)', 'typesafe', 'fixture-route', NULL,
                       'disabled', false, NULL, 'fixture: disabled on purpose',
                       'verify_ensemblesystemone fixture')""",
            S1_AVAILABLE, S1_FIXTURE_VERSION, S1_DISABLED,
        )
        await conn.execute(
            """INSERT INTO platform_model_catalog (model_id, display_name, provider, availability)
               VALUES ($1, 'ENSV fixture LLM (disabled)', 'anthropic', 'disabled')""",
            LLM_DISABLED,
        )


# ═══════════════════════════════════════════════════════════════════════════
# HTTP through the real app (TestClient, one per call, off the main loop)
# ═══════════════════════════════════════════════════════════════════════════
def _api(uid: UUID, method: str, path: str, body: dict | None = None):
    import main
    from starlette.testclient import TestClient

    sub = _sub(uid)
    main.verify_token = lambda _t: {"sub": sub, "email": f"{sub}@test.local", "org_id": str(ORG)}
    client = TestClient(main.app, raise_server_exceptions=False)
    client.__enter__()
    try:
        res = client.request(method, path, headers=HEADERS, json=body)
        try:
            return res.status_code, res.json()
        except Exception:  # noqa: BLE001
            return res.status_code, {"raw": res.text[:300]}
    finally:
        client.__exit__(None, None, None)


async def api(uid, method, path, body=None):
    return await asyncio.get_running_loop().run_in_executor(None, _api, uid, method, path, body)


def _detail(body) -> str:
    return str(body.get("detail", body))[:240] if isinstance(body, dict) else str(body)[:240]


# ═══════════════════════════════════════════════════════════════════════════
# Sections
# ═══════════════════════════════════════════════════════════════════════════
async def task1_findings(conn) -> dict:
    section("[Y] Task 1 findings (live)")
    facts: dict = {}

    # 1a — version, pass-through support, and how proxy config is stored.
    status, body = litellm("/openapi.json")
    version = json.loads(body).get("info", {}).get("version") if status == 200 else None
    check(version == "1.96.2", "1a. installed LiteLLM version is 1.96.2 (from the proxy's own /openapi.json)",
          f"version={version}")
    paths = json.loads(body).get("paths", {}) if status == 200 else {}
    has_pt_api = {"get", "post", "delete"} <= set(paths.get("/config/pass_through_endpoint", {}))
    check(has_pt_api, "1a. generic pass_through_endpoints admin API exists on 1.96.2 "
          "(GET/POST/DELETE /config/pass_through_endpoint)")
    status, body = litellm("/config/pass_through_endpoint")
    eps = json.loads(body).get("endpoints", []) if status == 200 else []
    ts = [e for e in eps if e.get("path") == "/typesafe"]
    ep = ts[0] if len(ts) == 1 else {}
    auth_ref = (ep.get("headers") or {}).get("Authorization")
    check(
        len(ts) == 1
        and (ep.get("target") or "").rstrip("/") == "https://api.typesafe.ai"
        and ep.get("include_subpath") is True
        and ep.get("auth") is not False
        and auth_ref == "Bearer os.environ/TYPESAFE_API_KEY",
        "1a. exactly one /typesafe pass-through: target https://api.typesafe.ai, include_subpath, "
        "auth required, Authorization header is an os.environ/TYPESAFE_API_KEY reference (not a value)",
        f"count={len(ts)} target={ep.get('target')} include_subpath={ep.get('include_subpath')} "
        f"auth={ep.get('auth')} header_is_reference={auth_ref == 'Bearer os.environ/TYPESAFE_API_KEY'}",
    )
    lite_names = doppler_names("prd_lite_llm")
    root_names = doppler_names("prd")
    check(lite_names is not None and root_names is not None,
          "Doppler secret NAMES readable for prd_lite_llm and prd")
    lite_names = lite_names or set()
    root_names = root_names or set()
    find("1a. proxy configuration storage",
         f"models AND pass-through endpoints are DB-stored on the proxy "
         f"(STORE_MODEL_IN_DB present in prd_lite_llm: {'STORE_MODEL_IN_DB' in lite_names}); changed via "
         f"the proxy admin API with the master key — scripts/configure_typesafe_passthrough.py; no config.yaml edit, "
         f"no redeploy, no upgrade")

    # 1b — what the previous run built, mapped.
    removed = [
        "apps/api/services/ai_model_catalog.py",
        "apps/api/scripts/verify_ensemblemodels.py",
    ]
    gone = [p for p in removed if not (REPO / p).exists()]
    check(len(gone) == len(removed), "1b. previous run's parallel LLM catalog service and its verify are removed",
          f"removed={gone}")
    find("1b. keep / reshape / remove",
         "KEEP: ai_ensemble_configs (trigger, one-active index, 4 policies), migrations/"
         "ensemblemodels_catalog_and_configs.sql (historical record). RESHAPE: ai_ensemble_configs columns; "
         "ai_judgment_models -> ai_system_one_models; routers/ai_ensembles.py; EnsemblePanel.jsx; "
         "lib/api.js + lib/noteTermsQueueActions.js; note-terms-queue page (unchanged wiring). REMOVE: "
         "services/ai_model_catalog.py, GET /admin/ai/model-catalog, comparison_kind, "
         "scripts/verify_ensemblemodels.py")

    # 1c — key name only.
    facts["key_present"] = "TYPESAFE_API_KEY" in lite_names
    find("1c. TYPESAFE_API_KEY in Doppler prd_lite_llm (name only)",
         "PRESENT" if facts["key_present"] else "ABSENT")

    # 1d — Jev versions TypeSafe reports, through the proxy.
    status, body = litellm("/typesafe/v1/models")
    listed = []
    if status == 200:
        try:
            listed = [m.get("name") for m in json.loads(body).get("models", []) if isinstance(m, dict)]
        except ValueError:
            listed = []
    facts["listed"] = listed
    if facts["key_present"]:
        check(status == 200 and bool(listed), "1d. models listing through the proxy returns Jev models",
              f"HTTP {status} models={listed}")
        find("1d. versions TypeSafe reports",
             f"{listed}. The listing names ALIASES only; the pinned '{JEV_ROUTE}' is not listed but is "
             f"callable, and is what jev-latest resolved to on 2026-10-01")
    else:
        blocked("1d. models listing", f"TYPESAFE_API_KEY absent from prd_lite_llm; HTTP {status}")
    return facts


async def schema_and_removal(conn) -> None:
    section("[Y] comparison_kind gone, columns renamed, no LLM-comparator code remains")
    cols = {r["column_name"] for r in await conn.fetch(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema = 'public' AND table_name = 'ai_ensemble_configs'"
    )}
    want = {"model_1", "model_1_version", "model_2", "model_2_version",
            "system_one_model", "system_one_model_version"}
    gone = {"comparison_kind", "review_model_1", "review_model_1_version", "review_model_2",
            "review_model_2_version", "comparison_model", "comparison_model_version"}
    check(want <= cols, "ai_ensemble_configs has model_1/2 + system_one_model and their *_version columns",
          f"missing={sorted(want - cols)}")
    check(not (gone & cols), "comparison_kind and every review_*/comparison_* column are gone",
          f"still present={sorted(gone & cols)}")
    notnull = {r["column_name"]: r["is_nullable"] for r in await conn.fetch(
        "SELECT column_name, is_nullable FROM information_schema.columns "
        "WHERE table_schema='public' AND table_name='ai_ensemble_configs' AND column_name = ANY($1::text[])",
        sorted(want),
    )}
    check(all(v == "NO" for v in notnull.values()) and len(notnull) == 6,
          "all three slots and their versions are NOT NULL", f"{notnull}")
    cons = {r["conname"]: r["def"] for r in await conn.fetch(
        "SELECT conname, pg_get_constraintdef(oid) AS def FROM pg_constraint "
        "WHERE conrelid = 'public.ai_ensemble_configs'::regclass"
    )}
    check("ai_ensemble_configs_models_distinct_chk" in cons and "ai_ensemble_configs_comparison_kind_chk" not in cons,
          "CHECK model_1 <> model_2 exists; the comparison_kind CHECK is gone", f"constraints={sorted(cons)}")
    check("ai_ensemble_configs_system_one_model_fkey" in cons
          and "ai_system_one_models" in cons.get("ai_ensemble_configs_system_one_model_fkey", ""),
          "system_one_model is an FK to ai_system_one_models(key)")
    tables = {r["t"] for r in await conn.fetch(
        "SELECT tablename AS t FROM pg_tables WHERE schemaname='public' "
        "AND tablename IN ('ai_judgment_models','ai_system_one_models')"
    )}
    check(tables == {"ai_system_one_models"}, "ai_judgment_models renamed to ai_system_one_models",
          f"tables={sorted(tables)}")
    s1cols = {r["column_name"] for r in await conn.fetch(
        "SELECT column_name FROM information_schema.columns "
        "WHERE table_schema='public' AND table_name='ai_system_one_models'"
    )}
    check({"model_route", "is_default", "last_check_detail", "model_version"} <= s1cols,
          "ai_system_one_models has model_route, is_default, last_check_detail", f"cols={sorted(s1cols)}")
    idx = await conn.fetchval(
        "SELECT indexdef FROM pg_indexes WHERE schemaname='public' "
        "AND indexname='ai_system_one_models_one_default'"
    )
    check(bool(idx) and "UNIQUE" in idx and "WHERE is_default" in idx,
          "partial UNIQUE index ai_system_one_models_one_default exists", f"{idx}")
    trig = await conn.fetchval(
        "SELECT count(*) FROM pg_trigger WHERE tgrelid='public.ai_ensemble_configs'::regclass "
        "AND tgname='ai_ensemble_configs_retire_only' AND NOT tgisinternal"
    )
    check(trig == 1, "the retire-only immutability trigger is still installed")
    one_active = await conn.fetchval(
        "SELECT indexdef FROM pg_indexes WHERE indexname='ai_ensemble_configs_one_active_per_task'"
    )
    check(bool(one_active) and "WHERE is_active" in one_active,
          "the one-active-per-task partial unique index is still installed")

    # Code: no LLM-comparator vocabulary anywhere in the app. The patterns are
    # assembled so this file does not match itself.
    patterns = ["comparison" + "_kind", "review" + "_model_1", "ai_" + "judgment_models",
                "ai_" + "model_catalog", "admin/ai/" + "model-catalog", "getAi" + "ModelCatalog",
                "comparison" + "_model", "KIND_" + "JUDGMENT"]
    files = subprocess.run(
        ["git", "ls-files", "apps/api", "apps/web"], cwd=REPO, capture_output=True, text=True
    ).stdout.split()
    # Untracked new files count too.
    files += subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard", "apps/api", "apps/web"],
        cwd=REPO, capture_output=True, text=True,
    ).stdout.split()
    allowed = {
        "apps/api/migrations/ensemblemodels_catalog_and_configs.sql",  # historical record of what was applied
        "apps/api/migrations/ensemblesystemone_reshape.sql",           # the rename itself
        "apps/api/scripts/verify_ensemblesystemone.py",               # names what it checks is gone
    }
    hits = []
    for f in sorted(set(files)):
        if f in allowed or not f.endswith((".py", ".js", ".jsx", ".ts", ".tsx", ".sql")):
            continue
        p = REPO / f
        if not p.is_file():
            continue
        text = p.read_text(errors="replace")
        for pat in patterns:
            if pat in text:
                hits.append(f"{f}:{pat}")
    check(not hits, "no LLM-comparator / comparison_kind / parallel-LLM-catalog code remains in apps/",
          f"hits={hits[:10]}")


async def rls_policies(conn) -> None:
    section("[Y] Four RLS policies on each table, re-verified after the DDL")
    bypass = await conn.fetchval("SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user")
    who = await conn.fetchval("SELECT current_user")
    check(bypass is False, "the verify connection's role does NOT bypass RLS (so enforcement below is real)",
          f"current_user={who} rolbypassrls={bypass}")
    for t in ("ai_ensemble_configs", "ai_system_one_models"):
        rows = await conn.fetch(
            "SELECT policyname, cmd, qual, with_check FROM pg_policies "
            "WHERE schemaname='public' AND tablename=$1 ORDER BY cmd", t
        )
        cmds = sorted(r["cmd"] for r in rows)
        check(cmds == ["DELETE", "INSERT", "SELECT", "UPDATE"],
              f"{t}: exactly four policies, one per operation", f"{[(r['policyname'], r['cmd']) for r in rows]}")
        writes_gated = all(
            "is_super_admin" in ((r["qual"] or "") + (r["with_check"] or ""))
            for r in rows if r["cmd"] != "SELECT"
        )
        check(writes_gated, f"{t}: INSERT/UPDATE/DELETE policies all check app.is_super_admin")
        rls_on = await conn.fetchval("SELECT relrowsecurity FROM pg_class WHERE oid = $1::regclass", f"public.{t}")
        check(rls_on is True, f"{t}: row level security is enabled")

    # Enforcement, not just presence: without context, an UPDATE of the real
    # Jev row matches zero rows (the "row not found" shape) and changes nothing.
    before = await rls_row(conn, "SELECT * FROM ai_system_one_models WHERE key = $1", JEV_KEY)
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'false', true)")
        result = await conn.execute(
            "UPDATE ai_system_one_models SET notes = notes WHERE key = $1", JEV_KEY
        )
    after = await rls_row(conn, "SELECT * FROM ai_system_one_models WHERE key = $1", JEV_KEY)
    check(result == "UPDATE 0" and before is not None and _norm(before) == _norm(after),
          "without super-admin context an UPDATE of the real Jev row matches ZERO rows and changes nothing",
          f"result={result}")
    err = None
    try:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.is_super_admin', 'false', true)")
            await conn.execute(
                "INSERT INTO ai_system_one_models (key, display_name, provider, model_route) "
                "VALUES ($1, 'x', 'typesafe', 'x')", S1_HTTP,
            )
    except Exception as exc:  # noqa: BLE001
        err = exc
    check(isinstance(err, asyncpg.InsufficientPrivilegeError),
          "without super-admin context an INSERT is refused by RLS", f"raised={type(err).__name__}")


async def one_default(conn) -> None:
    section("[Y] A second System One default is refused by the database")
    real_default = await rls_val(conn, "SELECT key FROM ai_system_one_models WHERE is_default")
    check(real_default == JEV_KEY, "the real default is Jev before the attempt", f"default={real_default}")
    err = await rls_expect_error(
        conn,
        "INSERT INTO ai_system_one_models (key, display_name, provider, model_route, is_default) "
        "VALUES ($1, 'ENSV second default', 'typesafe', 'x', true)",
        S1_SECOND_DEFAULT,
    )
    check(isinstance(err, asyncpg.UniqueViolationError)
          and getattr(err, "constraint_name", None) == "ai_system_one_models_one_default",
          "INSERT of a second is_default row is refused by ai_system_one_models_one_default",
          f"raised={type(err).__name__} constraint={getattr(err, 'constraint_name', None)}")
    err = await rls_expect_error(
        conn, "UPDATE ai_system_one_models SET is_default = true WHERE key = $1", S1_AVAILABLE
    )
    check(isinstance(err, asyncpg.UniqueViolationError),
          "UPDATE turning a fixture row into a second default is refused too", f"raised={type(err).__name__}")
    after = await rls_val(conn, "SELECT key FROM ai_system_one_models WHERE is_default")
    n = await rls_val(conn, "SELECT count(*) FROM ai_system_one_models WHERE is_default")
    check(after == JEV_KEY and n == 1, "the real default is still Jev, and still exactly one default",
          f"default={after} count={n}")


async def catalog_http(conn) -> None:
    section("[Y] super_admin edits the System One catalog; org_admin gets 403 on the identical request")
    body = {"key": S1_HTTP, "display_name": "ENSV http fixture", "provider": "typesafe",
            "model_route": "jev-latest", "notes": "verify_ensemblesystemone"}
    count = "SELECT count(*) FROM ai_system_one_models WHERE key = $1"

    s, b = await api(U_ORGADMIN, "GET", "/api/v1/admin/system-one-catalog")
    perms = b.get("permissions") if isinstance(b, dict) else None
    vocab = b.get("vocabularies") if isinstance(b, dict) else None
    check(s == 200 and perms is not None and perms.get("can_write") is False
          and vocab is not None and vocab.get("editable") == [] and vocab.get("manual_availability") == [],
          "org_admin GET: envelope says can_write=false with EMPTY editable/manual_availability (not omitted)",
          f"status={s} permissions={perms} vocabularies={vocab}")

    n0 = await rls_val(conn, count, S1_HTTP)
    s, b = await api(U_ORGADMIN, "POST", "/api/v1/admin/system-one-catalog", body)
    n1 = await rls_val(conn, count, S1_HTTP)
    check(s == 403 and n0 == 0 and n1 == 0, "org_admin POST (add entry) -> 403, and no row was written",
          f"status={s} rows before={n0} after={n1} detail={_detail(b)}")
    s, b = await api(U_SUPER, "POST", "/api/v1/admin/system-one-catalog", body)
    row = await rls_row(conn, "SELECT * FROM ai_system_one_models WHERE key = $1", S1_HTTP)
    check(s == 201 and row is not None and row["availability"] == "disabled" and row["is_default"] is False,
          "super_admin POST (identical body) -> 201; the row persisted (independent read), "
          "starting 'disabled' and not default",
          f"status={s} persisted={row is not None} availability={row['availability'] if row else None}")

    for method, path, payload, label in (
        ("PUT", f"/api/v1/admin/system-one-catalog/{S1_HTTP}/availability", {"availability": "deprecated"}, "set availability"),
        ("PUT", f"/api/v1/admin/system-one-catalog/{S1_HTTP}/default", None, "set default"),
        ("POST", f"/api/v1/admin/system-one-catalog/{S1_HTTP}/verify", None, "verify now"),
        ("DELETE", f"/api/v1/admin/system-one-catalog/{S1_HTTP}", None, "remove"),
    ):
        before = await rls_row(conn, "SELECT * FROM ai_system_one_models WHERE key = $1", S1_HTTP)
        default_before = await rls_val(conn, "SELECT key FROM ai_system_one_models WHERE is_default")
        s, b = await api(U_ORGADMIN, method, path, payload)
        after = await rls_row(conn, "SELECT * FROM ai_system_one_models WHERE key = $1", S1_HTTP)
        default_after = await rls_val(conn, "SELECT key FROM ai_system_one_models WHERE is_default")
        check(s == 403 and before is not None and after is not None and _norm(before) == _norm(after)
              and default_before == default_after,
              f"org_admin {method} ({label}) -> 403 and the entry and the default are unchanged",
              f"status={s} detail={_detail(b)}")

    s, b = await api(U_SUPER, "PUT", f"/api/v1/admin/system-one-catalog/{S1_HTTP}/availability",
                     {"availability": "deprecated"})
    av = await rls_val(conn, "SELECT availability FROM ai_system_one_models WHERE key = $1", S1_HTTP)
    check(s == 200 and av == "deprecated", "super_admin PUT availability=deprecated -> 200 and persisted",
          f"status={s} availability={av}")
    s, b = await api(U_SUPER, "PUT", f"/api/v1/admin/system-one-catalog/{S1_HTTP}/availability",
                     {"availability": "available"})
    av = await rls_val(conn, "SELECT availability FROM ai_system_one_models WHERE key = $1", S1_HTTP)
    check(s == 400 and av == "deprecated",
          "super_admin cannot set 'available' by hand (400) — only Verify now earns it",
          f"status={s} availability={av} detail={_detail(b)}")
    s, b = await api(U_SUPER, "DELETE", f"/api/v1/admin/system-one-catalog/{JEV_KEY}")
    still = await rls_val(conn, count, JEV_KEY)
    check(s == 400 and still == 1, "super_admin cannot remove the current default (Jev) — 400, row kept",
          f"status={s} detail={_detail(b)}")
    s, b = await api(U_SUPER, "DELETE", f"/api/v1/admin/system-one-catalog/{S1_HTTP}")
    n = await rls_val(conn, count, S1_HTTP)
    check(s == 200 and n == 0, "super_admin DELETE of the fixture entry -> 200 and the row is gone",
          f"status={s} rows={n}")


async def picker_and_refusals(conn) -> dict:
    section("[Y] The picker preselects the default System One model")
    s, b = await api(U_SUPER, "GET", f"/api/v1/admin/ai/ensembles?task_key={REAL_TASK}")
    default_key = await rls_val(conn, "SELECT key FROM ai_system_one_models WHERE is_default")
    pre = (b.get("preselected") or {}).get("system_one_model") if isinstance(b, dict) else None
    check(s == 200 and pre == default_key == JEV_KEY and b.get("default_system_one_model") == JEV_KEY,
          "GET /admin/ai/ensembles preselects the catalog default (Jev) in the System One slot",
          f"status={s} preselected={pre} db_default={default_key}")
    llm_ids = {o["model_id"] for o in (b.get("llm_options") or [])} if isinstance(b, dict) else set()
    s1_keys = {o["key"] for o in (b.get("system_one_options") or [])} if isinstance(b, dict) else set()
    check(not (llm_ids & s1_keys) and JEV_KEY in s1_keys and JEV_KEY not in llm_ids,
          "the two option lists are disjoint: Jev is only a System One option")
    voy = next((o for o in b.get("llm_options", []) if o["model_id"] == "voyage-3.5"), None) if isinstance(b, dict) else None
    check(voy is not None and voy["selectable"] is False and "chat" in (voy["unavailable_reason"] or ""),
          "voyage-3.5 is shown in the LLM list as unselectable, with the chat-mode reason",
          f"{voy}")
    jev = next((o for o in b.get("system_one_options", []) if o["key"] == JEV_KEY), None) if isinstance(b, dict) else None
    if jev is not None and not jev["selectable"]:
        check(bool(jev.get("unavailable_reason")),
              "Jev is unavailable and its picker option carries the reason",
              f"reason={jev.get('unavailable_reason')}")
        # With the fixtures removed, the real catalog alone offers no
        # selectable System One model: the picker's blocker must say so, and why.
        from services.ai_ensembles import blocker_for

        real_s1 = [o for o in b.get("system_one_options", []) if not o["key"].startswith(FIXTURE_PREFIX)]
        if not any(o["selectable"] for o in real_s1):
            msg = blocker_for(b.get("llm_options", []), real_s1) or ""
            check("No System One model is available" in msg and jev["display_name"] in msg
                  and (jev.get("unavailable_reason") or "") in msg,
                  "with no available System One model the picker's blocker says exactly why, naming Jev and its reason",
                  f"blocker={msg[:240]}")
    else:
        find("Jev was already available when the picker was read", "the blocker path was not exercised here")
    s_noauth, _ = await api(U_ORGADMIN, "GET", f"/api/v1/admin/ai/ensembles?task_key={REAL_TASK}")
    check(s_noauth == 403, "org_admin GET of the picker -> 403", f"status={s_noauth}")

    section("[Y] Model 1/2 refuse voyage-3.5, unavailable models, and Model 1 == Model 2")
    section_count = "SELECT count(*) FROM ai_ensemble_configs"

    async def refused(label, payload, needle):
        n0 = await rls_val(conn, section_count)
        st, bd = await api(U_SUPER, "POST", "/api/v1/admin/ai/ensembles", payload)
        n1 = await rls_val(conn, section_count)
        det = _detail(bd)
        check(st == 422 and n0 == n1 and needle in det, label,
              f"status={st} rows before={n0} after={n1} detail={det}")

    base = {"task_key": REAL_TASK, "model_1": "claude-haiku", "model_2": "claude-sonnet",
            "system_one_model": S1_AVAILABLE}
    await refused("Model 1 = voyage-3.5 is refused (not a chat model), nothing written",
                  {**base, "model_1": "voyage-3.5"}, "chat")
    await refused("Model 2 = voyage-3.5 is refused too",
                  {**base, "model_2": "voyage-3.5"}, "chat")
    await refused("Model 1 = a 'disabled' catalog LLM (fixture) is refused, nothing written",
                  {**base, "model_1": LLM_DISABLED}, "disabled")
    await refused("Model 1 == Model 2 is refused, nothing written",
                  {**base, "model_2": "claude-haiku"}, "different")
    err = await rls_expect_error(
        conn,
        "INSERT INTO ai_ensemble_configs (task_key, model_1, model_1_version, model_2, model_2_version, "
        "system_one_model, system_one_model_version) VALUES ($1,'a','v','a','v',$2,'v')",
        FIXTURE_TASK, S1_AVAILABLE,
    )
    check(isinstance(err, asyncpg.CheckViolationError)
          and getattr(err, "constraint_name", None) == "ai_ensemble_configs_models_distinct_chk",
          "the database itself refuses model_1 = model_2 (ai_ensemble_configs_models_distinct_chk)",
          f"raised={type(err).__name__}")

    section("[Y] The System One slot refuses an LLM and refuses a disabled entry")
    await refused("System One = claude-haiku (an LLM) is refused, nothing written",
                  {**base, "system_one_model": "claude-haiku"}, "LLM")
    await refused("System One = a 'disabled' entry is refused, nothing written",
                  {**base, "system_one_model": S1_DISABLED}, "not available")
    err = await rls_expect_error(
        conn,
        "INSERT INTO ai_ensemble_configs (task_key, model_1, model_1_version, model_2, model_2_version, "
        "system_one_model, system_one_model_version) VALUES ($1,'a','v','b','v','claude-haiku','v')",
        FIXTURE_TASK,
    )
    check(isinstance(err, asyncpg.ForeignKeyViolationError),
          "the database itself refuses an LLM in system_one_model (FK to ai_system_one_models)",
          f"raised={type(err).__name__}")
    s, b = await api(U_SUPER, "POST", "/api/v1/admin/ai/ensembles", {**base, "org_id": str(ORG)})
    check(s == 422, "a body carrying org_id is refused (extra='forbid')", f"status={s}")
    return b if isinstance(b, dict) else {}


async def activation(conn) -> None:
    section("[Y] Activation snapshots versions and retires the previous config in one transaction; "
            "the immutability trigger blocks edits")
    import services.ai_ensembles as ens
    from services.litellm_credentials import list_deployments

    # A fixture task key, so the real task's (empty) history is never touched.
    ens.KNOWN_TASK_KEYS = frozenset({*ens.KNOWN_TASK_KEYS, FIXTURE_TASK})

    upstream = {}
    for d in list_deployments():
        m = (d.get("litellm_params") or {}).get("model") or ""
        upstream.setdefault(d.get("model_name"), set()).add(m.split("/", 1)[1] if "/" in m else m)

    def expect(name):
        v = upstream.get(name) or set()
        return next(iter(v)) if len(v) == 1 else None

    real_before = await rls_val(conn, "SELECT count(*) FROM ai_ensemble_configs WHERE task_key = $1", REAL_TASK)

    # Activation runs on the raw connection in-process (the router's
    # KNOWN_TASK_KEYS patch would not cross into a TestClient), under
    # super-admin context, the way the request pool would set it.
    async def activate(m1, m2):
        async with conn.transaction():
            await _super(conn)
            return await ens.activate_ensemble(conn, FIXTURE_TASK, m1, m2, S1_AVAILABLE,
                                               created_by=U_SUPER, notes="verify_ensemblesystemone")

    try:
        r1 = await activate("claude-haiku", "claude-sonnet")
    except Exception as exc:  # noqa: BLE001
        check(False, "activation #1 succeeded", f"raised {type(exc).__name__}: {exc}")
        return
    c1 = await rls_row(conn, "SELECT * FROM ai_ensemble_configs WHERE id = $1", UUID(r1["ensemble"]["id"]))
    check(c1 is not None and c1["is_active"] is True and r1["retired_id"] is None,
          "activation #1 persisted as the active config (independent re-read), nothing to retire")
    check(c1 is not None
          and c1["model_1"] == "claude-haiku" and c1["model_1_version"] == expect("claude-haiku")
          and c1["model_2"] == "claude-sonnet" and c1["model_2_version"] == expect("claude-sonnet")
          and c1["system_one_model"] == S1_AVAILABLE and c1["system_one_model_version"] == S1_FIXTURE_VERSION,
          "the slots store CALLABLE names (deployment names / catalog key) and the *_version columns "
          "snapshot the exact upstream / reported version",
          f"m1={c1['model_1'] if c1 else None}@{c1['model_1_version'] if c1 else None} "
          f"m2={c1['model_2'] if c1 else None}@{c1['model_2_version'] if c1 else None} "
          f"s1_version_matches={c1['system_one_model_version'] == S1_FIXTURE_VERSION if c1 else None}")
    check(c1 is not None and c1["model_1"] != c1["model_1_version"],
          "the stored Model 1 value is the proxy deployment name, not the raw upstream id")

    r2 = await activate("claude-sonnet", "claude-haiku")
    c1b = await rls_row(conn, "SELECT * FROM ai_ensemble_configs WHERE id = $1", c1["id"])
    c2 = await rls_row(conn, "SELECT * FROM ai_ensemble_configs WHERE id = $1", UUID(r2["ensemble"]["id"]))
    n_active = await rls_val(conn, "SELECT count(*) FROM ai_ensemble_configs WHERE task_key=$1 AND is_active", FIXTURE_TASK)
    check(r2["retired_id"] == str(c1["id"]) and c1b["is_active"] is False and c1b["retired_at"] is not None
          and c2["is_active"] is True and n_active == 1,
          "activation #2 retired #1 and is the only active config for the task",
          f"retired_id matches={r2['retired_id'] == str(c1['id'])} active_count={n_active}")
    check({k: v for k, v in _norm(c1b).items() if k not in ("is_active", "retired_at")}
          == {k: v for k, v in _norm(c1).items() if k not in ("is_active", "retired_at")},
          "retiring #1 changed only is_active and retired_at")

    # One transaction: force the INSERT to fail AFTER the retire, and show the
    # retire did not survive.
    real_llm_options = ens.llm_options

    async def broken_llm_options(c):
        opts = await real_llm_options(c)
        for o in opts:
            if o["model_id"] == "claude-haiku":
                o["model_version"] = None  # NOT NULL on model_1_version -> INSERT fails
        return opts

    ens.llm_options = broken_llm_options
    raised = None
    try:
        async with conn.transaction():
            await _super(conn)
            try:
                await ens.activate_ensemble(conn, FIXTURE_TASK, "claude-haiku", "claude-sonnet",
                                            S1_AVAILABLE, created_by=U_SUPER)
            except Exception as exc:  # noqa: BLE001
                raised = exc
            # Still inside the outer transaction: activate's own unit has
            # rolled back, so the retire it performed must be gone.
            c2_mid = await conn.fetchrow("SELECT * FROM ai_ensemble_configs WHERE id = $1", c2["id"])
    finally:
        ens.llm_options = real_llm_options
    c2_after = await rls_row(conn, "SELECT * FROM ai_ensemble_configs WHERE id = $1", c2["id"])
    n_rows = await rls_val(conn, "SELECT count(*) FROM ai_ensemble_configs WHERE task_key=$1", FIXTURE_TASK)
    check(isinstance(raised, asyncpg.NotNullViolationError)
          and c2_mid is not None and c2_mid["is_active"] is True and c2_mid["retired_at"] is None
          and c2_after["is_active"] is True and n_rows == 2,
          "an INSERT failing after the retire rolls the retire back too — one transaction "
          "(#2 still active, still 2 rows)",
          f"raised={type(raised).__name__} active_mid={c2_mid['is_active'] if c2_mid else None} rows={n_rows}")

    for label, q in (
        ("editing notes", "UPDATE ai_ensemble_configs SET notes = 'edited' WHERE id = $1"),
        ("editing model_1", "UPDATE ai_ensemble_configs SET model_1 = 'claude-sonnet' WHERE id = $1"),
        ("editing a version snapshot", "UPDATE ai_ensemble_configs SET system_one_model_version = 'x' WHERE id = $1"),
    ):
        before = await rls_row(conn, "SELECT * FROM ai_ensemble_configs WHERE id = $1", c2["id"])
        err = await rls_expect_error(conn, q, c2["id"])
        after = await rls_row(conn, "SELECT * FROM ai_ensemble_configs WHERE id = $1", c2["id"])
        check(isinstance(err, asyncpg.CheckViolationError) and _norm(before) == _norm(after),
              f"the immutability trigger blocks {label} on the active config, row unchanged",
              f"raised={type(err).__name__}")
    err = await rls_expect_error(
        conn, "UPDATE ai_ensemble_configs SET is_active = true, retired_at = NULL WHERE id = $1", c1["id"]
    )
    check(isinstance(err, asyncpg.CheckViolationError),
          "a retired config cannot be re-activated in place", f"raised={type(err).__name__}")
    err = await rls_expect_error(
        conn,
        "INSERT INTO ai_ensemble_configs (task_key, model_1, model_1_version, model_2, model_2_version, "
        "system_one_model, system_one_model_version, is_active) VALUES ($1,'a','v','b','v',$2,'v',true)",
        FIXTURE_TASK, S1_AVAILABLE,
    )
    check(isinstance(err, asyncpg.UniqueViolationError),
          "a second active config for the same task is refused by the partial unique index",
          f"raised={type(err).__name__}")
    real_after = await rls_val(conn, "SELECT count(*) FROM ai_ensemble_configs WHERE task_key = $1", REAL_TASK)
    check(real_before == real_after, "the real task's configs were not touched by any of this",
          f"before={real_before} after={real_after}")


async def jev_live(conn, facts: dict) -> None:
    section("[Y] Jev: real call through the proxy")
    if not facts.get("key_present"):
        av = await rls_val(conn, "SELECT availability FROM ai_system_one_models WHERE key = $1", JEV_KEY)
        check(av == "disabled", "TYPESAFE_API_KEY absent -> Jev stays 'disabled'", f"availability={av}")
        blocked("Jev live check", "TYPESAFE_API_KEY is absent from Doppler prd_lite_llm — Joe adds it")
        return

    from services.system_one import probe_proxy

    probe = probe_proxy("typesafe", JEV_ROUTE)
    decision = probe.get("decision") or {}
    answer = next(iter((decision.get("answers") or {}).values()), {}) if isinstance(decision, dict) else {}
    probs = answer.get("probabilities") if isinstance(answer, dict) else None
    intact = (isinstance(probs, dict) and len(probs) == 2
              and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in probs.values())
              and math.isclose(sum(probs.values()), 1.0, abs_tol=1e-3))
    check(probe["ok"] is True and bool(probe["listed_models"]),
          "a real models listing through the proxy succeeded", f"listed={probe['listed_models']} detail={probe['detail']}")
    check(intact and decision.get("model") == JEV_ROUTE,
          "one real decision on the pinned route came back with probabilities intact "
          "(a float per option, summing to 1) and ran exactly jev-1.13.0",
          f"model={decision.get('model')} probabilities={probs}")

    s, b = await api(U_SUPER, "POST", f"/api/v1/admin/system-one-catalog/{JEV_KEY}/verify")
    row = await rls_row(conn, "SELECT * FROM ai_system_one_models WHERE key = $1", JEV_KEY)
    fresh = await conn.fetchval("SELECT now() - $1::timestamptz < interval '10 minutes'",
                                row["last_verified_at"]) if row and row["last_verified_at"] else False
    check(s == 200 and isinstance(b, dict) and b.get("ok") is True and row is not None
          and row["availability"] == "available" and row["model_version"] == JEV_ROUTE and fresh is True,
          "Verify now through the real API made Jev 'available', recording last_verified_at (just now) "
          "and the reported version jev-1.13.0 (independent re-read)",
          f"status={s} ok={b.get('ok') if isinstance(b, dict) else None} availability={row['availability'] if row else None} "
          f"model_version={row['model_version'] if row else None} detail={_detail(b) if s != 200 else b.get('detail')}")
    s, b = await api(U_SUPER, "GET", f"/api/v1/admin/ai/ensembles?task_key={REAL_TASK}")
    jev = next((o for o in b.get("system_one_options", []) if o["key"] == JEV_KEY), None) if isinstance(b, dict) else None
    check(jev is not None and jev["selectable"] is True,
          "the picker now offers Jev as selectable", f"{jev and jev.get('unavailable_reason')}")


async def key_custody(conn, facts: dict) -> None:
    section("[Y] The TypeSafe key never appears in application code or the app's environment; "
            "the request carries only a LiteLLM key")
    root = doppler_names("prd") or set()
    check("TYPESAFE_API_KEY" not in root, "TYPESAFE_API_KEY is NOT a secret in Doppler root prd (the app's config)")
    check("TYPESAFE_API_KEY" not in os.environ,
          "TYPESAFE_API_KEY is not in this process's hydrated app environment")

    # Code: nothing in the app talks to TypeSafe directly.
    direct = []
    for d in ("services", "routers"):
        for p in (API_DIR / d).rglob("*.py"):
            if "api.typesafe.ai" in p.read_text(errors="replace"):
                direct.append(str(p.relative_to(REPO)))
    check(not direct, "no service or router calls https://api.typesafe.ai directly (only the proxy route does)",
          f"hits={direct}")

    if not facts.get("key_present"):
        blocked("key value scan", "TYPESAFE_API_KEY absent from prd_lite_llm — nothing to scan for")
    else:
        secret = doppler_secret_value("prd_lite_llm", "TYPESAFE_API_KEY")
        if not secret:
            check(False, "TYPESAFE_API_KEY value readable from prd_lite_llm for the comparison (not printed)")
        else:
            in_env = [k for k, v in os.environ.items() if secret in (v or "")]
            check(not in_env, "the key's VALUE appears in no app environment variable (count only)",
                  f"matching variables={len(in_env)}")
            files = subprocess.run(["git", "ls-files", "apps", "docs", "sprint_prompts"], cwd=REPO,
                                   capture_output=True, text=True).stdout.split()
            files += subprocess.run(["git", "ls-files", "--others", "--exclude-standard", "apps", "docs"],
                                    cwd=REPO, capture_output=True, text=True).stdout.split()
            leaks = 0
            for f in set(files):
                p = REPO / f
                try:
                    if p.is_file() and p.stat().st_size < 5_000_000 and secret in p.read_text(errors="ignore"):
                        leaks += 1
                except OSError:
                    continue
            check(leaks == 0, "the key's VALUE appears in no repo file under apps/, docs/, sprint_prompts/ (count only)",
                  f"files containing it={leaks}")

        # What the app actually sends: capture every outgoing request during
        # one probe, without printing any header value.
        from services.system_one import probe_proxy

        captured = []
        real_urlopen = urllib.request.urlopen

        def spy(req, *a, **kw):
            if isinstance(req, urllib.request.Request):
                captured.append((req.full_url, dict(req.header_items())))
            return real_urlopen(req, *a, **kw)

        urllib.request.urlopen = spy
        try:
            probe_proxy("typesafe", JEV_ROUTE)
        finally:
            urllib.request.urlopen = real_urlopen
        lite_host = urllib.parse.urlparse(os.environ["LITELLM_BASE_URL"]).hostname
        hosts = {urllib.parse.urlparse(u).hostname for u, _ in captured}
        auths = [h.get("Authorization") for _, h in captured]
        only_lite = all(a == f"Bearer {os.environ['LITELLM_MASTER_KEY']}" for a in auths)
        no_secret = (not secret) or all(secret not in json.dumps(h) for _, h in captured)
        check(len(captured) == 2 and hosts == {lite_host} and only_lite and no_secret,
              "the app's two requests go ONLY to the LiteLLM proxy and carry ONLY the LiteLLM key "
              "(never the TypeSafe key)",
              f"requests={len(captured)} hosts_are_proxy={hosts == {lite_host}} "
              f"auth_is_litellm_key={only_lite} typesafe_key_absent={no_secret}")
        secret = None  # noqa: F841 — drop the reference

    s, _ = litellm("/typesafe/v1/models", auth=False)
    check(s == 401, "the proxy refuses /typesafe with no LiteLLM key (auth required on the route)", f"HTTP {s}")


# ═══════════════════════════════════════════════════════════════════════════
async def main_async() -> int:
    dsn = await bootstrap_async()
    if not dsn:
        print("[SKIP] no working DATABASE_URL — nothing can be proven")
        return 2
    for name in ("LITELLM_BASE_URL", "LITELLM_MASTER_KEY"):
        if not os.environ.get(name):
            print(f"[FAIL] {name} not hydrated from Doppler")
            return 1

    from services.action_registry import REGISTRY

    # main.py's startup hook syncs the action catalog into the REAL default
    # org on every TestClient entry. No-op it for this process.
    original_sync = REGISTRY.sync_catalog

    async def _noop_sync(pool, org_id):
        return None

    REGISTRY.sync_catalog = _noop_sync

    conn = await asyncpg.connect(dsn, statement_cache_size=0, ssl="require")
    applied = await conn.fetchval(
        "SELECT to_regclass('public.ai_system_one_models') IS NOT NULL"
    )
    if not check(applied is True, "migrations/ensemblesystemone_reshape.sql is applied "
                 "(ai_system_one_models exists)"):
        print("\nApply apps/api/migrations/ensemblesystemone_reshape.sql, then re-run.")
        REGISTRY.sync_catalog = original_sync
        await conn.close()
        print(f"TOTAL: {_n_pass} PASS, {_n_fail} FAIL")
        return 1

    baseline = None
    try:
        await teardown(conn)
        baseline = await snapshot_real(conn)
        counts0 = {
            t: await rls_val(conn, f"SELECT count(*) FROM {t}")
            for t in ("ai_ensemble_configs", "ai_system_one_models", "platform_model_catalog")
        }
        check(baseline["default"] == JEV_KEY, "baseline: Jev is the real default", f"default={baseline['default']}")

        facts = await task1_findings(conn)
        await schema_and_removal(conn)
        await rls_policies(conn)

        section("Fixtures")
        await seed(conn)
        n = await rls_val(conn, "SELECT count(*) FROM ai_system_one_models WHERE key LIKE $1", FIXTURE_PREFIX + "%")
        check(n == 2, "fixture System One entries seeded", f"rows={n}")

        await one_default(conn)
        await catalog_http(conn)
        await picker_and_refusals(conn)
        await activation(conn)
        await jev_live(conn, facts)
        await key_custody(conn, facts)
    finally:
        section("[Y] Teardown: zero leftover rows; real catalog rows and the real default untouched")
        try:
            await teardown(conn)
            leftovers = {
                "configs": await rls_val(conn, "SELECT count(*) FROM ai_ensemble_configs WHERE task_key = $1 "
                                               "OR system_one_model LIKE $2", FIXTURE_TASK, FIXTURE_PREFIX + "%"),
                "system_one": await rls_val(conn, "SELECT count(*) FROM ai_system_one_models WHERE key LIKE $1",
                                            FIXTURE_PREFIX + "%"),
                "llm": await rls_val(conn, "SELECT count(*) FROM platform_model_catalog WHERE model_id LIKE $1",
                                     FIXTURE_PREFIX + "%"),
                "users": await rls_val(conn, "SELECT count(*) FROM users WHERE id = ANY($1::uuid[])", FIXTURE_USERS),
                "org": await rls_val(conn, "SELECT count(*) FROM organizations WHERE id = $1", ORG),
                "roles": await rls_val(conn, "SELECT count(*) FROM roles WHERE org_id = $1", ORG),
            }
            check(all(v == 0 for v in leftovers.values()), "zero fixture rows remain", f"{leftovers}")
            if baseline is not None:
                counts1 = {
                    t: await rls_val(conn, f"SELECT count(*) FROM {t}")
                    for t in ("ai_ensemble_configs", "ai_system_one_models", "platform_model_catalog")
                }
                check(counts0 == counts1, "exact before/after row counts match on all three tables",
                      f"before={counts0} after={counts1}")
                after = await snapshot_real(conn)
                check(after["llm"] == baseline["llm"], "real platform_model_catalog rows are byte-identical")
                check(after["cfg"] == baseline["cfg"], "real ai_ensemble_configs rows are byte-identical")

                def strip(rows):
                    return [{k: v for k, v in r.items()
                             if not (r.get("key") == JEV_KEY and k in JEV_CHECK_COLUMNS)} for r in rows]

                check(strip(after["s1"]) == strip(baseline["s1"]),
                      "real System One rows are byte-identical, except Jev's four verification columns "
                      "which the live check is meant to update")
                check(after["default"] == baseline["default"] == JEV_KEY,
                      "the real default is still Jev", f"default={after['default']}")
        finally:
            REGISTRY.sync_catalog = original_sync
            await conn.close()

    print(f"\n{'=' * 70}")
    if _blocked:
        print(f"BLOCKED (not counted as FAIL): {len(_blocked)} — {_blocked}")
    print(f"TOTAL: {_n_pass} PASS, {_n_fail} FAIL")
    print("=" * 70)
    return 0 if _ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main_async()))
