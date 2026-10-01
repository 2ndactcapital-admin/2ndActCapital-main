"""verify_ensemblemodels.py — ensemblemodels.structural.

Proves the super-admin ensemble-model selection: the union model catalog
(LiteLLM /model/info + ai_judgment_models), the immutable versioned
ai_ensemble_configs table, and the three /api/v1/admin/ai/ endpoints.

Pass/fail only, no prompts, idempotent. Teardown by fixture tag at start AND
end, with a before/after count of every NON-fixture row as the backstop
(never a TRUNCATE).

Secrets: hydrated from Doppler over HTTPS (scripts/_doppler_env.py). A
hydration failure is FATAL — this script never runs on ambient env.

Database: every direct DB step runs on APP_SERVICE_DATABASE_URL (role
app_service, rolbypassrls asserted false). If it cannot connect the script
FAILS LOUDLY and exits — it NEVER falls back to another role or URL.
Super-admin context for fixture writes comes only from
services.database.platform_scope (SET LOCAL app.is_super_admin inside one
transaction) — the app's own mechanism, never a bypass role.

Fixtures:
  * an org + two users (one super_admin, one plain member) for the HTTP 403 /
    200 proofs, driven through the real ASGI app with main.verify_token
    patched (the verify_litellmphasef.py pattern);
  * an 'available' judgment model (FIX_JUDGE). Only two LiteLLM chat models
    exist today (claude-haiku, claude-sonnet) and the comparison model must
    differ from both, so no real three-model ensemble can be activated; the
    fixture judge fills the comparison slot so retirement can be proven.
  * a fixture task_key (FIX_TASK), added to KNOWN_TASK_KEYS IN THIS PROCESS
    ONLY, so activation tests can never retire a real note_terms_hazard
    selection.

Cost: zero model calls. Reads /model/info and /openapi.json only.

Run:  python3 apps/api/scripts/verify_ensemblemodels.py
"""
from __future__ import annotations

import asyncio
import json
import os
import pathlib
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from uuid import UUID

HERE = pathlib.Path(__file__).resolve()
API_DIR = HERE.parents[1]
REPO = HERE.parents[3]
for _sp in sorted(API_DIR.glob("venv/lib/python3*/site-packages")):
    if str(_sp) not in sys.path:
        sys.path.insert(0, str(_sp))
for _p in (str(API_DIR), str(HERE.parent)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

HEADERS = {"Authorization": "Bearer verify-token"}
EXPECTED_LITELLM_VERSION = "1.96.2"
REAL_TASK = "note_terms_hazard"

FIX_TASK = "__verify_ensemblemodels__"
FIX_JUDGE = "__verify_ensemblemodels_judge__"
FIX_ORG_ID = UUID("99000000-0000-0000-0000-0000000e5e01")
FIX_SUPER_ID = UUID("99000000-0000-0000-0000-0000000e5e02")
FIX_MEMBER_ID = UUID("99000000-0000-0000-0000-0000000e5e03")
FIX_SUPER_SUB = "auth0|verify_ensemblemodels_superadmin"
FIX_MEMBER_SUB = "auth0|verify_ensemblemodels_member"

REVIEW_DISTINCT_CHK = "ai_ensemble_configs_review_models_distinct_chk"
COMPARISON_DISTINCT_CHK = "ai_ensemble_configs_comparison_distinct_chk"
RETIRE_ONLY = "ai_ensemble_configs_retire_only"

# Files this sprint wrote or edited (secret scan, check 12).
SPRINT_FILES = [
    "apps/api/migrations/ensemblemodels_catalog_and_configs.sql",
    "apps/api/services/ai_model_catalog.py",
    "apps/api/routers/ai_ensembles.py",
    "apps/api/main.py",
    "apps/api/scripts/verify_ensemblemodels.py",
    "apps/web/lib/api.js",
    "apps/web/lib/noteTermsQueueActions.js",
    "apps/web/components/admin/EnsemblePanel.jsx",
    "apps/web/app/admin/pricing/note-terms-queue/page.js",
    "docs/PROJECT_STATUS.md",
]
# Known credential PREFIXES only — never derived from the Doppler values.
SECRET_PATTERNS = [
    ("anthropic key", re.compile(r"sk-ant-[A-Za-z0-9_\-]{10,}")),
    ("openai-style key", re.compile(r"\bsk-[A-Za-z0-9]{20,}")),
    ("litellm virtual key", re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}")),
    ("doppler token", re.compile(r"\bdp\.(?:st|ct|pt|sa|scim|audit)\.[A-Za-z0-9]{10,}")),
    ("voyage key", re.compile(r"\bpa-[A-Za-z0-9_\-]{30,}")),
    ("aws access key id", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("supabase secret key", re.compile(r"\bsb_secret_[A-Za-z0-9_\-]{10,}")),
    ("postgres url with password", re.compile(r"postgres(?:ql)?://[^:/\s]+:[^@\s]{8,}@")),
]

_n_pass = 0
_n_fail = 0
_n_find = 0


def check(label: str, passed: bool, detail: str = "") -> bool:
    global _n_pass, _n_fail
    print(f"{'[PASS]' if passed else '[FAIL]'} {label}" + (f"  — {detail}" if detail else ""))
    if passed:
        _n_pass += 1
    else:
        _n_fail += 1
    return passed


def find(label: str, detail: str = "") -> None:
    """A real discovery, neither PASS nor FAIL — reported, never fixed here."""
    global _n_find
    print(f"[FIND] {label}" + (f"  — {detail}" if detail else ""))
    _n_find += 1


def fatal(msg: str) -> int:
    print(f"\n[FATAL] {msg}")
    print(f"\nRESULT: FAIL (aborted)  {_n_pass} passed, {_n_fail + 1} failed")
    return 2


def litellm_get(path: str) -> tuple[int, object]:
    base = os.environ["LITELLM_BASE_URL"].rstrip("/")
    req = urllib.request.Request(
        base + path, headers={"Authorization": f"Bearer {os.environ['LITELLM_MASTER_KEY']}"}
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, None


class Principal:
    def __init__(self, client, sub, org_id):
        self.client, self.sub, self.org_id = client, sub, str(org_id)

    def call(self, method, path, body=None):
        import main

        sub, org_id = self.sub, self.org_id
        main.verify_token = lambda _t: {"sub": sub, "email": f"{sub}@test.local", "org_id": org_id}
        fn = getattr(self.client, method)
        return fn(path, headers=HEADERS, **({"json": body} if body is not None else {}))


async def counts_outside_fixtures(conn) -> dict:
    return {
        "ai_ensemble_configs": await conn.fetchval(
            "SELECT count(*) FROM ai_ensemble_configs WHERE task_key <> $1", FIX_TASK),
        "ai_judgment_models": await conn.fetchval(
            "SELECT count(*) FROM ai_judgment_models WHERE key <> $1", FIX_JUDGE),
        "users": await conn.fetchval(
            "SELECT count(*) FROM users WHERE org_id <> $1", FIX_ORG_ID),
    }


async def teardown(conn) -> None:
    from services.database import platform_scope

    async with platform_scope(conn):
        await conn.execute("DELETE FROM ai_ensemble_configs WHERE task_key = $1", FIX_TASK)
        await conn.execute("DELETE FROM ai_judgment_models WHERE key = $1", FIX_JUDGE)
        await conn.execute(
            "DELETE FROM user_roles WHERE user_id IN (SELECT id FROM users WHERE org_id = $1)",
            FIX_ORG_ID)
        await conn.execute("DELETE FROM users WHERE org_id = $1", FIX_ORG_ID)
        await conn.execute("DELETE FROM organizations WHERE id = $1", FIX_ORG_ID)


async def setup(conn) -> None:
    from services.database import platform_scope

    async with platform_scope(conn):
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3)",
            FIX_ORG_ID, "Verify Ensemble Models Org", "verify-ensemblemodels-org")
        for uid, sub, role in ((FIX_SUPER_ID, FIX_SUPER_SUB, "super_admin"),
                               (FIX_MEMBER_ID, FIX_MEMBER_SUB, "member")):
            await conn.execute(
                "INSERT INTO users (id, org_id, email, full_name, auth0_sub, role) "
                "VALUES ($1, $2, $3, $4, $5, $6)",
                uid, FIX_ORG_ID, f"{sub}@test.local", f"Verify {role}", sub, role)
        await conn.execute(
            "INSERT INTO ai_judgment_models (key, display_name, provider, model_version, "
            "availability, last_verified_at, notes) "
            "VALUES ($1, 'Verify fixture judge', 'verify-fixture', 'fixture-1', "
            "'available', now(), 'verify_ensemblemodels fixture — deleted by teardown')",
            FIX_JUDGE)


async def insert_raw(conn, m1, m2, comp, *, task=FIX_TASK, super_admin=True):
    """Direct INSERT, bypassing every app-layer check. Returns the exception
    raised (or None). Always rolled back."""
    tr = conn.transaction()
    await tr.start()
    try:
        if super_admin:
            await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        await conn.execute(
            "INSERT INTO ai_ensemble_configs (task_key, review_model_1, review_model_1_version, "
            "review_model_2, review_model_2_version, comparison_model, comparison_model_version, "
            "comparison_kind) VALUES ($1, $2, 'v', $3, 'v', $4, 'v', 'llm')",
            task, m1, m2, comp)
        return None
    except Exception as exc:  # noqa: BLE001
        return exc
    finally:
        await tr.rollback()


async def update_raw(conn, sql, *args):
    """Run one UPDATE under super-admin context. Returns (exception|None,
    rows_affected). Rolled back unless it succeeded AND commit is wanted —
    here always rolled back, so a wrongly-permitted update changes nothing."""
    tr = conn.transaction()
    await tr.start()
    try:
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        status = await conn.execute(sql, *args)
        return None, int(status.split()[-1])
    except Exception as exc:  # noqa: BLE001
        return exc, 0
    finally:
        await tr.rollback()


def _constraint(exc) -> str | None:
    return getattr(exc, "constraint_name", None)


async def main() -> int:
    print("=== verify_ensemblemodels — ensemblemodels.structural ===\n")

    # ── Secrets: Doppler only ─────────────────────────────────────────────
    from _doppler_env import hydrate_from_doppler

    names, err = hydrate_from_doppler()
    if err:
        return fatal(f"Doppler hydration failed ({err}). Refusing to run on ambient env.")
    print(f"[doppler] hydrated {len(names)} secret names "
          f"(config: {os.environ.get('DOPPLER_CONFIG', '?')})")
    for var in ("APP_SERVICE_DATABASE_URL", "DATABASE_URL", "LITELLM_BASE_URL", "LITELLM_MASTER_KEY"):
        if not os.environ.get(var):
            return fatal(f"{var} not present after Doppler hydration.")

    # ── RLS connection: APP_SERVICE_DATABASE_URL, no fallback ─────────────
    import asyncpg

    try:
        conn = await asyncpg.connect(
            os.environ["APP_SERVICE_DATABASE_URL"], statement_cache_size=0, ssl="require", timeout=30)
    except Exception as exc:  # noqa: BLE001
        pw = urllib.parse.unquote(
            urllib.parse.urlsplit(os.environ["APP_SERVICE_DATABASE_URL"].strip()).password or "")
        hint = ""
        if isinstance(exc, asyncpg.InvalidPasswordError):
            hint = (" Its embedded password "
                    + ("MATCHES" if pw == os.environ.get("DB_PASSWORD") else "does NOT match")
                    + " Doppler's current DB_PASSWORD secret — if it does not match, the URL "
                    "holds a stale hand-typed password; rebuild it in Doppler as a "
                    "${DB_PASSWORD} reference.")
        return fatal(
            f"cannot connect via APP_SERVICE_DATABASE_URL: {type(exc).__name__}.{hint} "
            f"NOT falling back to any other role or URL — RLS checks are meaningless "
            f"on an unverified connection.")

    try:
        role = await conn.fetchval("SELECT current_user")
        bypass = await conn.fetchval("SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user")
        if not check("RLS connection is app_service with rolbypassrls = false",
                     role == "app_service" and bypass is False, f"role={role} bypassrls={bypass}"):
            return fatal("RLS connection is not a non-bypassing app_service role.")

        import services.ai_model_catalog as amc

        amc.KNOWN_TASK_KEYS = frozenset(amc.KNOWN_TASK_KEYS | {FIX_TASK})  # this process only

        await teardown(conn)
        before = await counts_outside_fixtures(conn)
        await setup(conn)

        # ── 11. LiteLLM version ───────────────────────────────────────────
        print("\n--- LiteLLM ---")
        st, spec = litellm_get("/openapi.json")
        ver = (spec or {}).get("info", {}).get("version") if st == 200 else None
        check("11. installed LiteLLM is still 1.96.2", ver == EXPECTED_LITELLM_VERSION,
              f"/openapi.json info.version = {ver!r} (HTTP {st})")

        st, mi = litellm_get("/model/info")
        if st != 200:
            return fatal(f"GET /model/info -> HTTP {st}; the catalog cannot be checked.")
        # Every platform model group -> its upstream ids, modes and providers,
        # read straight from /model/info (independent of the service code).
        all_groups: dict[str, dict] = {}
        for d in mi["data"]:
            name = d.get("model_name")
            if name and not re.match(r"^org-[a-z0-9_]+-[0-9a-f-]{36}$", name):
                up = (d.get("litellm_params") or {}).get("model") or ""
                info = d.get("model_info") or {}
                g = all_groups.setdefault(name, {"ups": set(), "modes": set(), "providers": set()})
                g["ups"].add(up.split("/", 1)[-1])
                g["modes"].add(info.get("mode"))
                g["providers"].add(info.get("litellm_provider") or up.split("/", 1)[0])
        # The catalog's LLM half is chat-mode groups only.
        groups = {k: g["ups"] for k, g in all_groups.items() if g["modes"] == {"chat"}}
        embedding_groups = sorted(k for k, g in all_groups.items() if "embedding" in g["modes"])

        # ── HTTP section (real ASGI app) ──────────────────────────────────
        from starlette.testclient import TestClient
        import main as main_module

        with TestClient(main_module.app, raise_server_exceptions=False) as client:
            sup = Principal(client, FIX_SUPER_SUB, FIX_ORG_ID)
            mem = Principal(client, FIX_MEMBER_SUB, FIX_ORG_ID)

            # ── 1. catalog ────────────────────────────────────────────────
            print("\n--- catalog ---")
            r = sup.call("get", "/api/v1/admin/ai/model-catalog")
            rows = r.json().get("rows", []) if r.status_code == 200 else []
            llm = {e["key"]: e for e in rows if e["kind"] == "llm"}
            check("1a. super_admin GET /api/v1/admin/ai/model-catalog -> 200", r.status_code == 200,
                  f"HTTP {r.status_code} {r.text[:200] if r.status_code != 200 else ''}")
            check("1b. catalog lists every chat-mode /model/info model group (count matches)",
                  set(llm) == set(groups) and len(llm) == len(groups),
                  f"catalog={sorted(llm)} /model/info chat={sorted(groups)}")

            # 1c. No embedding model in the catalog — checked against both the
            # catalog's own mode field and /model/info's embedding groups.
            leaked = sorted(
                {e["key"] for e in rows if e.get("mode") == "embedding"}
                | (set(llm) & set(embedding_groups))
            )
            check("1c. no embedding-mode model appears in the catalog",
                  r.status_code == 200 and not leaked,
                  f"leaked={leaked}" if leaked else
                  f"/model/info embedding groups {embedding_groups} all excluded"
                  + ("" if embedding_groups else " (none exist — exclusion not exercised)"))

            # 1d. Anthropic upstream ids must be dated snapshots. An undated
            # id (claude-sonnet-4-6) is an alias Anthropic can repoint, so a
            # stored selection would not pin a version. Reported, not fixed:
            # LiteLLM config is out of scope for this script.
            anthropic = {
                k: sorted(all_groups[k]["ups"]) for k in sorted(groups)
                if "anthropic" in all_groups[k]["providers"]
            }
            undated = sorted(
                up for ups in anthropic.values() for up in ups
                if not re.search(r"-\d{8}$", up)
            )
            if undated:
                find("1d. Anthropic model(s) with an UNDATED upstream id (alias, not a pinned version)",
                     "; ".join(f"{k} -> {', '.join(ups)}" for k, ups in anthropic.items()
                               if set(ups) & set(undated))
                     + f"  undated ids: {undated}")
            else:
                check("1d. every Anthropic upstream id carries a date suffix",
                      bool(anthropic), "; ".join(f"{k} -> {', '.join(ups)}" for k, ups in anthropic.items())
                      or "no Anthropic chat models in /model/info")

            # 1e. every chat catalog entry's version is EXACTLY the upstream
            # model id /model/info reports for that group — independently
            # re-derived here from /model/info, not trusted from the API.
            version_mismatches = []
            for key, ups in groups.items():
                entry = llm.get(key)
                if entry is None:
                    continue
                expected = next(iter(ups)) if len(ups) == 1 else None
                if entry.get("model_version") != expected:
                    version_mismatches.append(
                        f"{key}: catalog={entry.get('model_version')!r} /model/info={expected!r}")
            check("1e. every chat catalog entry's version matches /model/info's upstream id",
                  r.status_code == 200 and not version_mismatches,
                  "; ".join(version_mismatches) if version_mismatches
                  else f"{len(groups)} chat group(s) verified")

            # ── 2. Jev ────────────────────────────────────────────────────
            jev = next((e for e in rows if e["key"] == "typesafe-jev"), None)
            jev_db = await conn.fetchrow(
                "SELECT availability, last_verified_at FROM ai_judgment_models WHERE key = 'typesafe-jev'")
            ok_jev = (
                jev is not None and jev_db is not None and jev["kind"] == "judgment"
                and (jev_db["availability"] != "available" or jev_db["last_verified_at"] is not None)
                and jev["availability"] == jev_db["availability"]
            )
            check("2. Jev present; not 'available' unless a real call succeeded (last_verified_at set)",
                  ok_jev, f"api={jev and jev['availability']} db={dict(jev_db) if jev_db else None}")

            # ── 9a. non-super-admin refused over HTTP ─────────────────────
            print("\n--- access control ---")
            n_real_before = await conn.fetchval(
                "SELECT count(*) FROM ai_ensemble_configs WHERE task_key IN ($1, $2)", REAL_TASK, FIX_TASK)
            r = mem.call("post", "/api/v1/admin/ai/ensembles", {
                "task_key": FIX_TASK, "review_model_1": "claude-haiku",
                "review_model_2": "claude-sonnet", "comparison_model": FIX_JUDGE})
            n_after = await conn.fetchval(
                "SELECT count(*) FROM ai_ensemble_configs WHERE task_key IN ($1, $2)", REAL_TASK, FIX_TASK)
            check("9a. POST /ensembles as a non-super-admin -> 403, nothing written",
                  r.status_code == 403 and n_after == n_real_before,
                  f"HTTP {r.status_code}; rows {n_real_before}->{n_after}")
            r = mem.call("get", "/api/v1/admin/ai/model-catalog")
            check("9b. GET /model-catalog as a non-super-admin -> 403", r.status_code == 403,
                  f"HTTP {r.status_code}")
            r = sup.call("post", "/api/v1/admin/ai/ensembles", {
                "task_key": REAL_TASK, "review_model_1": "claude-haiku",
                "review_model_2": "claude-sonnet", "comparison_model": "claude-opus",
                "org_id": str(FIX_ORG_ID)})
            check("9c. a body declaring org_id is refused (extra='forbid')", r.status_code == 422,
                  f"HTTP {r.status_code}")

            # ── 3/4/5/6. API-layer refusals (super_admin) ─────────────────
            print("\n--- API-layer validation ---")
            def post(m1, m2, comp, task=REAL_TASK):
                return sup.call("post", "/api/v1/admin/ai/ensembles", {
                    "task_key": task, "review_model_1": m1, "review_model_2": m2,
                    "comparison_model": comp})

            n0 = await conn.fetchval("SELECT count(*) FROM ai_ensemble_configs")
            cases = [
                ("3a. API rejects review_model_1 = review_model_2",
                 ("claude-haiku", "claude-haiku", FIX_JUDGE), "different"),
                ("4a. API rejects comparison_model = review_model_1",
                 ("claude-haiku", "claude-sonnet", "claude-haiku"), "comparison"),
                ("4b. API rejects comparison_model = review_model_2",
                 ("claude-haiku", "claude-sonnet", "claude-sonnet"), "comparison"),
                ("5a. API refuses an unavailable model (Jev, 'disabled') as comparison",
                 ("claude-haiku", "claude-sonnet", "typesafe-jev"), "not available"),
                ("5b. API refuses a non-chat model (embedding) as a review model",
                 ("claude-haiku", "voyage-3.5", FIX_JUDGE), "not in the model catalog"),
                ("6. API refuses a judgment model (an AVAILABLE one) as a review model",
                 ("claude-haiku", FIX_JUDGE, "claude-sonnet"), "judgment"),
            ]
            for label, (m1, m2, comp), needle in cases:
                task = FIX_TASK
                r = post(m1, m2, comp, task)
                detail = r.json().get("detail", "") if r.headers.get("content-type", "").startswith("application/json") else r.text
                check(label, r.status_code == 422 and needle in str(detail),
                      f"HTTP {r.status_code}: {str(detail)[:140]}")
            n1 = await conn.fetchval("SELECT count(*) FROM ai_ensemble_configs")
            check("5c. no refused request wrote a row", n0 == n1, f"rows {n0}->{n1}")

            # ── 7. B retires A ────────────────────────────────────────────
            print("\n--- activation / retirement ---")
            rA = post("claude-haiku", "claude-sonnet", FIX_JUDGE, FIX_TASK)
            a_id = rA.json().get("ensemble", {}).get("id") if rA.status_code == 201 else None
            check("7a. super_admin activates config A -> 201", rA.status_code == 201 and a_id,
                  f"HTTP {rA.status_code} {rA.text[:200] if rA.status_code != 201 else ''}")
            warn = rA.json().get("warnings", []) if rA.status_code == 201 else []
            check("7b. same-provider review models -> a warning in the response, not a block",
                  rA.status_code == 201 and any("provider family" in w for w in warn), f"{warn}")
            a_snapshot = await conn.fetchval(
                "SELECT (to_jsonb(c) - 'is_active' - 'retired_at')::text FROM ai_ensemble_configs c WHERE id = $1",
                UUID(a_id)) if a_id else None
            a_versions = await conn.fetchrow(
                "SELECT review_model_1_version, review_model_2_version FROM ai_ensemble_configs WHERE id = $1",
                UUID(a_id)) if a_id else None
            check("7c. A snapshots the exact upstream versions at activation",
                  bool(a_versions) and {a_versions[0]} == groups.get("claude-haiku")
                  and {a_versions[1]} == groups.get("claude-sonnet"),
                  f"{dict(a_versions) if a_versions else None}")

            rB = post("claude-sonnet", "claude-haiku", FIX_JUDGE, FIX_TASK)
            b_id = rB.json().get("ensemble", {}).get("id") if rB.status_code == 201 else None
            check("7d. super_admin activates config B -> 201, reports A as retired",
                  rB.status_code == 201 and rB.json().get("retired_id") == a_id,
                  f"HTTP {rB.status_code} retired_id={rB.json().get('retired_id') if rB.status_code == 201 else None}")
            actives = await conn.fetch(
                "SELECT id FROM ai_ensemble_configs WHERE task_key = $1 AND is_active", FIX_TASK)
            check("7e. exactly one active row for the task_key, and it is B",
                  len(actives) == 1 and b_id and str(actives[0]["id"]) == b_id,
                  f"active={[str(x['id']) for x in actives]}")
            a_now = await conn.fetchrow(
                "SELECT is_active, retired_at, (to_jsonb(c) - 'is_active' - 'retired_at')::text AS rest "
                "FROM ai_ensemble_configs c WHERE id = $1", UUID(a_id)) if a_id else None
            check("7f. A is retired (is_active=false, retired_at set) and every other column "
                  "is byte-for-byte unchanged",
                  bool(a_now) and a_now["is_active"] is False and a_now["retired_at"] is not None
                  and a_now["rest"] == a_snapshot,
                  "identical" if a_now and a_now["rest"] == a_snapshot else f"before={a_snapshot} after={a_now and a_now['rest']}")

            r = sup.call("get", f"/api/v1/admin/ai/ensembles?task_key={REAL_TASK}")
            check("7g. GET /ensembles?task_key=note_terms_hazard -> 200 envelope with active + rows",
                  r.status_code == 200 and "active" in r.json() and isinstance(r.json().get("rows"), list),
                  f"HTTP {r.status_code}")

        # ── 3/4 table layer (CHECK constraints by name) ───────────────────
        print("\n--- table-layer CHECKs (direct INSERT, super-admin context, rolled back) ---")
        exc = await insert_raw(conn, "m-a", "m-a", "m-c")
        check("3b. table CHECK rejects review_model_1 = review_model_2",
              isinstance(exc, asyncpg.CheckViolationError) and _constraint(exc) == REVIEW_DISTINCT_CHK,
              f"{type(exc).__name__ if exc else 'ACCEPTED'} constraint={_constraint(exc)}")
        for lbl, args in (("4c", ("m-a", "m-b", "m-a")), ("4d", ("m-a", "m-b", "m-b"))):
            exc = await insert_raw(conn, *args)
            check(f"{lbl}. table CHECK rejects comparison_model = review model ({args[2]})",
                  isinstance(exc, asyncpg.CheckViolationError) and _constraint(exc) == COMPARISON_DISTINCT_CHK,
                  f"{type(exc).__name__ if exc else 'ACCEPTED'} constraint={_constraint(exc)}")
        exc = await insert_raw(conn, "m-a", "m-b", "m-c")
        check("3c. control: a valid row passes the CHECKs (proves 3b/4c/4d are not vacuous)",
              exc is None, f"{type(exc).__name__ if exc else 'accepted, rolled back'}")

        # ── 8. immutability ───────────────────────────────────────────────
        print("\n--- immutability (each attempt rolled back) ---")
        if a_id and b_id:
            A, B = UUID(a_id), UUID(b_id)
            b_before = await conn.fetchval("SELECT to_jsonb(c)::text FROM ai_ensemble_configs c WHERE id = $1", B)
            attempts = [
                ("8a. editing notes on the active row raises",
                 "UPDATE ai_ensemble_configs SET notes = 'edited' WHERE id = $1", B),
                ("8b. changing a model on the active row raises",
                 "UPDATE ai_ensemble_configs SET review_model_1 = 'claude-opus' WHERE id = $1", B),
                ("8c. retiring AND changing another column in one update raises",
                 "UPDATE ai_ensemble_configs SET is_active = false, retired_at = now(), notes = 'x' WHERE id = $1", B),
                ("8d. retiring without setting retired_at raises",
                 "UPDATE ai_ensemble_configs SET is_active = false WHERE id = $1", B),
                ("8e. re-activating a retired row raises",
                 "UPDATE ai_ensemble_configs SET is_active = true, retired_at = NULL WHERE id = $1", A),
                ("8f. editing a retired row raises",
                 "UPDATE ai_ensemble_configs SET comparison_model = 'other' WHERE id = $1", A),
            ]
            for label, sql, rid in attempts:
                exc, _ = await update_raw(conn, sql, rid)
                check(label, isinstance(exc, asyncpg.CheckViolationError) and _constraint(exc) == RETIRE_ONLY,
                      f"{type(exc).__name__ if exc else 'PERMITTED'} constraint={_constraint(exc)}")
            exc, n = await update_raw(
                conn, "UPDATE ai_ensemble_configs SET is_active = false, retired_at = now() WHERE id = $1", B)
            check("8g. control: a clean retirement IS permitted (proves 8a-f are not vacuous)",
                  exc is None and n == 1, f"{type(exc).__name__ if exc else 'ok'} rows={n} (rolled back)")
            b_after = await conn.fetchval("SELECT to_jsonb(c)::text FROM ai_ensemble_configs c WHERE id = $1", B)
            check("8h. B is unchanged after every attempt", b_before == b_after)
        else:
            check("8. immutability", False, "skipped — config A/B were not created in step 7")

        # ── 9d. RLS refuses a direct insert without super-admin ───────────
        print("\n--- RLS (app_service, no super-admin context) ---")
        exc = await insert_raw(conn, "m-a", "m-b", "m-c", super_admin=False)
        check("9d. direct INSERT without is_super_admin is rejected by RLS",
              isinstance(exc, asyncpg.InsufficientPrivilegeError) and "row-level security" in str(exc),
              f"{type(exc).__name__ if exc else 'ACCEPTED'}: {str(exc)[:120] if exc else ''}")
        tr = conn.transaction()
        await tr.start()
        try:
            exc_j = None
            try:
                await conn.execute(
                    "INSERT INTO ai_judgment_models (key, display_name, provider, model_version) "
                    "VALUES ('__verify_rls_probe__', 'x', 'x', 'x')")
            except Exception as e:  # noqa: BLE001
                exc_j = e
        finally:
            await tr.rollback()
        check("9e. direct INSERT into ai_judgment_models without is_super_admin is rejected by RLS",
              isinstance(exc_j, asyncpg.InsufficientPrivilegeError),
              f"{type(exc_j).__name__ if exc_j else 'ACCEPTED'}")

        # ── 10. global read, no org context ───────────────────────────────
        tr = conn.transaction()
        await tr.start()
        try:
            guc_org = await conn.fetchval("SELECT current_setting('app.current_org_id', true)")
            guc_sa = await conn.fetchval("SELECT current_setting('app.is_super_admin', true)")
            n_j = await conn.fetchval("SELECT count(*) FROM ai_judgment_models")
            n_c = await conn.fetchval("SELECT count(*) FROM ai_ensemble_configs WHERE task_key = $1", FIX_TASK)
        finally:
            await tr.rollback()
        check("10. app_service with NO org / super-admin context reads both tables",
              not guc_org and guc_sa != "true" and n_j >= 2 and n_c == 2,
              f"org_guc={guc_org!r} super_guc={guc_sa!r} judgment_rows={n_j} fixture_configs={n_c}")

        # ── 12. secret scan ───────────────────────────────────────────────
        print("\n--- secret scan ---")
        hits, missing = [], []
        for rel in SPRINT_FILES:
            p = REPO / rel
            if not p.exists():
                missing.append(rel)
                continue
            text = p.read_text(encoding="utf-8", errors="replace")
            for name, rx in SECRET_PATTERNS:
                if rx.search(text):
                    hits.append(f"{rel}: {name}")
        check("12a. every sprint file exists to be scanned", not missing, f"missing={missing}")
        check("12b. no known credential prefix appears in any file this sprint wrote",
              not hits, f"{hits}" if hits else f"{len(SPRINT_FILES) - len(missing)} files clean")

        # ── teardown + backstop ───────────────────────────────────────────
        await teardown(conn)
        after = await counts_outside_fixtures(conn)
        left = await conn.fetchval(
            "SELECT (SELECT count(*) FROM ai_ensemble_configs WHERE task_key = $1)"
            " + (SELECT count(*) FROM ai_judgment_models WHERE key = $2)"
            " + (SELECT count(*) FROM users WHERE org_id = $3)", FIX_TASK, FIX_JUDGE, FIX_ORG_ID)
        check("teardown: zero fixture rows remain, non-fixture row counts unchanged",
              left == 0 and before == after, f"left={left} before={before} after={after}")
    finally:
        try:
            await teardown(conn)
        except Exception as exc:  # noqa: BLE001
            print(f"[WARN] final teardown failed: {type(exc).__name__}")
        await conn.close()

    print(f"\nRESULT: {'PASS' if _n_fail == 0 else 'FAIL'}  {_n_pass} passed, {_n_fail} failed, "
          f"{_n_find} finding(s)")
    return 0 if _n_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
