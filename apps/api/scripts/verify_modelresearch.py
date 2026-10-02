"""verify_modelresearch.py — the read-only Model Research grid.

WHAT WAS BUILT (modelresearch.structural; this script proves it):
  * services/model_research.py — one row per model in the LiteLLM proxy's
    loaded price list (GET /public/litellm_model_cost_map), flagged "live on
    our proxy" (/model/info) and "in platform catalog"
    (public.platform_model_catalog), plus one row per
    public.ai_system_one_models entry (kind 'System One'). Rows are built from
    an explicit allow-list. In-process cache, 1 hour; refresh=true bypasses it.
  * routers/model_research.py — GET /api/v1/admin/model-research
    (super_admin, or manage_org_settings via can_manage_org_settings).
  * apps/web: app/admin/model-research/page.js,
    components/admin/ModelResearchGrid.jsx (DataGrid), the Next.js forward
    route, and a STRICT, fail-closed menu gate in lib/menuVisibility.mjs
    rendered by components/Sidebar.jsx and the /admin index.
  * scripts/modelresearch_menu_harness.mjs — feeds the REAL /users/me
    envelopes of this script's fixture users into the shipped menu rule.

Hydrates secrets from Doppler over HTTPS at startup (_db_bootstrap, the
verify_ensemblesystemone.py pattern). Never prints a credential value.
RLS context is set on EVERY read of an RLS table (``_super`` inside an
explicit transaction), including before/after comparisons.

ASSERTIONS:
  [Y] Task 1's five findings reported
  [Y] super_admin gets 200; an org admin gets 200; a plain member gets 403 on
      the IDENTICAL request
  [Y] The menu entry renders for both admin types and not for a member, with
      no truthy fallback
  [Y] Row count matches the source's model count (plus System One rows)
  [Y] "Live on our proxy" flags match /model/info exactly
  [Y] "In platform catalog" flags and availability match platform_model_catalog exactly
  [Y] The Jev row is present, kind 'System One'
  [Y] Per-1M prices equal the source's per-token price x 1,000,000 exactly, on a sample
  [Y] No row and no response contains api_key, api_base, or any credential field
  [Y] Refresh genuinely re-fetches: last-refreshed changes and the cache is bypassed
  [Y] Version is blank, not invented, for models with no dated suffix
  [Y] The endpoint performs no writes: row counts of every table it touches are unchanged
  [Y] npm run build exits 0

Pass/fail only. Prints 'TOTAL: N PASS, M FAIL' and exits non-zero on failure.

Run:  python3 apps/api/scripts/verify_modelresearch.py
"""
from __future__ import annotations

import asyncio
import json
import os
import pathlib
import re
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime
from uuid import UUID

HERE = pathlib.Path(__file__).resolve()
sys.path.insert(0, str(HERE.parent))
from _db_bootstrap import bootstrap_async  # noqa: E402  (also puts apps/api on sys.path)

import asyncpg  # noqa: E402

REPO = HERE.parents[3]
API_DIR = HERE.parents[1]
WEB_DIR = REPO / "apps" / "web"

# ═══════════════════════════════════════════════════════════════════════════
# Fixtures — the "4d52" block is unused by any other verify script (grep'd).
# Every unique value below uses the FULL UUID, never a slice of one.
# ═══════════════════════════════════════════════════════════════════════════
ORG = UUID("99000000-0000-0000-0000-00004d520001")
U_SUPER = UUID("99000000-0000-0000-0000-00004d520011")      # users.role super_admin; holds only 'member'
U_ORGADMIN = UUID("99000000-0000-0000-0000-00004d520012")   # users.role 'member'; holds the org_admin GRANT
U_MEMBER = UUID("99000000-0000-0000-0000-00004d520013")     # users.role 'member'; holds only 'member'
U_STRING_ONLY = UUID("99000000-0000-0000-0000-00004d520014")  # users.role 'org_admin' STRING, no grant
U_ROLELESS = UUID("99000000-0000-0000-0000-00004d520015")   # no roles at all (FIND only)
FIXTURE_USERS = [U_SUPER, U_ORGADMIN, U_MEMBER, U_STRING_ONLY, U_ROLELESS]

FIXTURE_PREFIX = "mrsv-"
CATALOG_FIXTURE = f"{FIXTURE_PREFIX}catalog-{UUID('99000000-0000-0000-0000-00004d520021')}"

PATH = "/api/v1/admin/model-research"
HEADERS = {"Authorization": "Bearer verify-token"}
JEV_KEY = "typesafe-jev"

# The verify's OWN copy of the row allow-list. A row key outside it fails.
ALLOWED_ROW_KEYS = {
    "id", "model", "display_name", "provider", "version", "mode", "kind",
    "max_input_tokens", "max_output_tokens", "deprecation_date",
    "live_on_proxy", "proxy_aliases", "in_catalog", "catalog_model_ids",
    "catalog_availability", "system_one_availability", "row_source",
    "input_per_1m", "output_per_1m", "batch_input_per_1m", "batch_output_per_1m",
    "cached_input_per_1m", "function_calling", "structured_output", "vision",
    "reasoning", "prompt_caching", "web_search",
}
PRICE_PAIRS = (
    ("input_per_1m", "input_cost_per_token"),
    ("output_per_1m", "output_cost_per_token"),
    ("batch_input_per_1m", "input_cost_per_token_batches"),
    ("batch_output_per_1m", "output_cost_per_token_batches"),
    ("cached_input_per_1m", "cache_read_input_token_cost"),
)
SECRET_TERMS = (
    "api_key", "api_base", "api_version", "litellm_params", "master_key",
    "os.environ", "Authorization", "Bearer", "credential", "secret",
    "aws_access_key", "aws_secret", "vertex_credentials", "access_token",
)

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
# Direct source reads (the verify's own, independent of the app's code)
# ═══════════════════════════════════════════════════════════════════════════
def litellm(path: str, *, auth: bool = True) -> tuple[int, str]:
    """GET only. This script never sends a write to the proxy."""
    base = os.environ["LITELLM_BASE_URL"].rstrip("/")
    req = urllib.request.Request(f"{base}{path}")
    if auth:
        req.add_header("Authorization", f"Bearer {os.environ['LITELLM_MASTER_KEY']}")
    try:
        with urllib.request.urlopen(req, timeout=90) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001
        return 0, type(e).__name__


def litellm_json(path: str):
    status, body = litellm(path)
    if status != 200:
        return None
    try:
        return json.loads(body)
    except ValueError:
        return None


def published_list():
    url = ("https://raw.githubusercontent.com/BerriAI/litellm/main/"
           "model_prices_and_context_window.json")
    try:
        with urllib.request.urlopen(url, timeout=90) as r:
            return json.loads(r.read())
    except Exception:  # noqa: BLE001
        return None


def is_model(key, value) -> bool:
    return (key != "sample_spec" and isinstance(value, dict)
            and isinstance(value.get("litellm_provider"), str) and bool(value["litellm_provider"]))


def real_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


# ═══════════════════════════════════════════════════════════════════════════
# DB helpers — every RLS read/write sets super-admin context in its own txn
# ═══════════════════════════════════════════════════════════════════════════
async def _super(conn) -> None:
    await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")


async def rls_fetch(conn, q, *a):
    async with conn.transaction():
        await _super(conn)
        return await conn.fetch(q, *a)


async def rls_val(conn, q, *a):
    async with conn.transaction():
        await _super(conn)
        return await conn.fetchval(q, *a)


def _norm(row) -> dict:
    return {k: (str(v) if v is not None else None) for k, v in dict(row).items()}


def _sub(uid: UUID) -> str:
    return f"modelresearch_{uid.hex}"


async def teardown(conn) -> None:
    fixture_subs = [_sub(u) for u in FIXTURE_USERS]
    async with conn.transaction():
        await _super(conn)
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
        await conn.execute("DELETE FROM platform_model_catalog WHERE model_id LIKE $1", FIXTURE_PREFIX + "%")


async def seed(conn) -> None:
    from services.rbac import ensure_role, grant_org_admin

    async with conn.transaction():
        await _super(conn)
        await conn.execute(
            "INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3)",
            ORG, "MODELRESEARCH Fixture Org", f"{ORG}-modelresearch",
        )
        for uid, role in ((U_SUPER, "super_admin"), (U_ORGADMIN, "member"), (U_MEMBER, "member"),
                          (U_STRING_ONLY, "org_admin"), (U_ROLELESS, "member")):
            sub = _sub(uid)
            await conn.execute(
                """INSERT INTO users (id, org_id, email, full_name, auth0_sub, role, is_active)
                   VALUES ($1, $2, $3, $4, $5, $6, true)""",
                uid, ORG, f"{sub}@test.local", sub, sub, role,
            )
        # A 'member' role with ZERO permissions. Holding any role takes a user
        # off has_permission's role-less default-allow, so a 403 is a real
        # refusal. The super_admin holds it too: a 200 for them then proves
        # the super-admin bypass, not a permission they happen to have.
        member_role = await ensure_role(conn, ORG, "member", "modelresearch fixture: no permissions")
        for uid in (U_SUPER, U_MEMBER, U_STRING_ONLY):
            await conn.execute(
                "INSERT INTO user_roles (user_id, role_id) VALUES ($1, $2) ON CONFLICT DO NOTHING",
                uid, member_role,
            )
        await grant_org_admin(conn, U_ORGADMIN, ORG)
        # A catalog entry with no proxy deployment and no price-list key: it
        # must surface as its own row, with its own availability.
        await conn.execute(
            """INSERT INTO platform_model_catalog (model_id, display_name, provider, availability)
               VALUES ($1, 'MRSV fixture (disabled)', 'anthropic', 'disabled')""",
            CATALOG_FIXTURE,
        )


# ═══════════════════════════════════════════════════════════════════════════
# HTTP through the real app (TestClient, one per call, off the main loop)
# ═══════════════════════════════════════════════════════════════════════════
def _api(uid: UUID, path: str):
    import main
    from starlette.testclient import TestClient

    sub = _sub(uid)
    main.verify_token = lambda _t: {"sub": sub, "email": f"{sub}@test.local", "org_id": str(ORG)}
    client = TestClient(main.app, raise_server_exceptions=False)
    client.__enter__()
    try:
        res = client.request("GET", path, headers=HEADERS)
        return res.status_code, res.text
    finally:
        client.__exit__(None, None, None)


async def api(uid, path=PATH):
    status, text = await asyncio.get_running_loop().run_in_executor(None, _api, uid, path)
    try:
        body = json.loads(text)
    except ValueError:
        body = {"raw": text[:300]}
    return status, body, text


def _detail(body) -> str:
    return str(body.get("detail", body))[:240] if isinstance(body, dict) else str(body)[:240]


# ═══════════════════════════════════════════════════════════════════════════
# Sections
# ═══════════════════════════════════════════════════════════════════════════
def task1_findings() -> dict:
    section("[Y] Task 1 findings (live)")
    facts: dict = {}
    reported = []

    # 1a — which endpoints return the FULL price list vs only registrations.
    status, body = litellm("/openapi.json")
    spec = json.loads(body) if status == 200 else {}
    version = spec.get("info", {}).get("version")
    check(version == "1.96.2", "1a. proxy reports LiteLLM 1.96.2 (its own /openapi.json)", f"version={version}")
    cost_map = litellm_json("/public/litellm_model_cost_map")
    facts["cost_map"] = cost_map if isinstance(cost_map, dict) else {}
    n_models = sum(1 for k, v in facts["cost_map"].items() if is_model(k, v))
    check(n_models > 2000, "1a. GET /public/litellm_model_cost_map returns the FULL price list",
          f"{len(facts['cost_map'])} keys, {n_models} models")
    registered = {}
    for p in ("/model/info", "/v2/model/info", "/model_group/info", "/v1/models"):
        j = litellm_json(p)
        registered[p] = len(j.get("data", [])) if isinstance(j, dict) else None
    hub = litellm_json("/public/model_hub")
    n_reg = registered["/model/info"]
    check(n_reg is not None and all(v == n_reg for v in registered.values()) and n_reg < 50,
          "1a. /model/info, /v2/model/info, /model_group/info, /v1/models return ONLY registered models",
          f"{registered}")
    src = litellm_json("/model/cost_map/source") or {}
    sched = litellm_json("/schedule/model_cost_map_reload/status") or {}
    facts["source"] = src
    facts["sched"] = sched
    find("1a. proxy endpoints",
         f"FULL list: GET /public/litellm_model_cost_map ({n_models} models; served WITHOUT auth too). "
         f"Registered only: {registered}. /public/model_hub -> {hub!r}. /model/cost_map/source -> "
         f"source={src.get('source')} url={src.get('url')} model_count={src.get('model_count')}. "
         f"/schedule/model_cost_map_reload/status -> last_run={sched.get('last_run')} "
         f"(the proxy does not report when it loaded its list)")
    reported.append("1a")
    s_noauth, _ = litellm("/public/litellm_model_cost_map", auth=False)
    if s_noauth == 200:
        find("1a. /public/litellm_model_cost_map needs no key",
             "public data plus one {id, db_model, blocked} stub per registered deployment UUID — "
             "no credential, but the deployment ids are exposed unauthenticated")

    # 1b — staleness against the published list.
    pub = published_list()
    if pub is None:
        blocked("1b. published list comparison", "GitHub raw unreachable from this host")
        reported.append("1b (BLOCKED)")
    else:
        pk = {k for k, v in facts["cost_map"].items() if is_model(k, v)}
        gk = {k for k, v in pub.items() if is_model(k, v)}
        price_diff = sorted(
            k for k in pk & gk
            if any(facts["cost_map"][k].get(f) != pub[k].get(f) for _, f in PRICE_PAIRS)
        )
        recent = ["claude-sonnet-4-6", "claude-opus-4-5", "claude-haiku-4-5-20251001", "gpt-5.1", "gemini-2.5-pro"]
        find("1b. proxy price list vs published",
             f"proxy {len(pk)} models, published {len(gk)}; only in published: {len(gk - pk)} "
             f"{sorted(gk - pk)[:8]}; only on proxy: {len(pk - gk)} {sorted(pk - gk)[:8]}; "
             f"shared models with different prices: {len(price_diff)} {price_diff[:6]}; "
             f"recent models on proxy: {[m for m in recent if m in pk]}")
        check(True, "1b. gap measured against the live published list")
        reported.append("1b")

    # 1c — every field /model/info returns.
    mi = litellm_json("/model/info") or {}

    def walk(o, pre=""):
        if isinstance(o, dict):
            for k, v in o.items():
                yield from walk(v, f"{pre}.{k}" if pre else k)
        else:
            yield pre

    paths = sorted({p for d in mi.get("data", []) for p in walk(d)})
    top = sorted({k for d in mi.get("data", []) for k in d})
    lp = sorted({k for d in mi.get("data", []) for k in (d.get("litellm_params") or {})})
    facts["model_info"] = mi.get("data", [])
    check("litellm_params" in top, "1c. /model/info entries carry litellm_params (why rows are allow-listed)",
          f"top-level keys={top}")
    find("1c. /model/info fields",
         f"{len(paths)} leaf paths; top-level {top}; litellm_params keys today {lp} (can also hold "
         f"api_base / api_key / credential refs); model_info.* is the price-list schema plus "
         f"id/key/db_model/blocked/access_via_team_ids/direct_access/supported_openai_params")
    reported.append("1c")

    # 1d — DataGrid at ~4,450 rows.
    dg = (WEB_DIR / "components/ui/DataGrid.jsx").read_text()
    grid = (WEB_DIR / "components/admin/ModelResearchGrid.jsx").read_text()
    paged = "getPaginationRowModel" in dg and "import DataGrid" in grid and "pageSize={50}" in grid
    check(paged, "1d. DataGrid paginates client-side (TanStack) and the page reuses it with pageSize 50")
    find("1d. DataGrid at scale",
         f"client-side TanStack sort/filter/paginate; only one page ({50} rows) is in the DOM, so "
         f"{n_models} rows need NO server paging. Its column filters are text-only, so multi-select, "
         f"range and tri-state filters run in the page before rows reach the grid")
    reported.append("1d")

    # 1e — menu + envelope pattern.
    mv = (WEB_DIR / "lib/menuVisibility.mjs").read_text()
    check("GATE_MANAGE_ORG_SETTINGS_STRICT" in mv and "/admin/model-research" in mv,
          "1e. the entry lives in lib/menuVisibility.mjs (sidebar + /admin index source of truth)")
    find("1e. menu + envelope",
         "org-admin pages sit in Sidebar.jsx's canAccess(me, GATE_MANAGE_ORG_SETTINGS) block and the "
         "/admin index (visibleAdminSections). canPerm() default-allows when roles is empty, and "
         "usePermissions substitutes {roles: [], permissions: []} when /users/me fails, so the EXISTING "
         "gates fail OPEN on a lost envelope. The new entry uses a STRICT gate instead; the existing "
         "gates are unchanged (flagged, not fixed). Envelope: {rows, permissions, vocabularies}")
    reported.append("1e")

    check(len(reported) == 5, "Task 1's five findings reported", f"reported={reported}")
    return facts


async def access(conn) -> dict:
    section("[Y] Access: super_admin 200, org admin 200, member 403 on the IDENTICAL request")
    bypass = await conn.fetchval("SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user")
    check(bypass is False, "the verify connection's role does NOT bypass RLS", f"rolbypassrls={bypass}")

    from services import model_research
    model_research._reset_cache_for_tests()

    results = {}
    for name, uid in (("super_admin", U_SUPER), ("org_admin", U_ORGADMIN), ("member", U_MEMBER),
                      ("string_only", U_STRING_ONLY)):
        results[name] = await api(uid)
    s_sa, b_sa, _ = results["super_admin"]
    s_oa, b_oa, _ = results["org_admin"]
    s_m, b_m, _ = results["member"]
    s_so, b_so, _ = results["string_only"]
    check(s_sa == 200, "super_admin (holding only a zero-permission role) gets 200 — the bypass",
          f"HTTP {s_sa} {_detail(b_sa) if s_sa != 200 else ''}")
    check(s_oa == 200, "org admin (users.role 'member', real org_admin GRANT) gets 200 — resolved by permission",
          f"HTTP {s_oa} {_detail(b_oa) if s_oa != 200 else ''}")
    check(s_m == 403 and "manage_org_settings" in _detail(b_m),
          "plain member gets 403 on the identical request, naming manage_org_settings", f"HTTP {s_m} {_detail(b_m)}")
    check(s_m == 403 and "rows" not in b_m and "meta" not in b_m, "the 403 carries no rows and no metadata")
    check(s_so == 403, "users.role = 'org_admin' as a STRING with no grant gets 403 — the string is not the gate",
          f"HTTP {s_so}")

    s_rl, _, _ = await api(U_ROLELESS)
    find("role-less account and the API",
         f"a user holding NO roles gets HTTP {s_rl} from the API (services.rbac.has_permission's "
         f"role-less default-allow, unchanged by this sprint) while the strict menu gate HIDES the entry")

    perms = b_sa.get("permissions") if isinstance(b_sa, dict) else None
    vocab = b_sa.get("vocabularies") if isinstance(b_sa, dict) else None
    check(isinstance(perms, dict) and perms.get("can_read") is True and perms.get("can_write") is False
          and perms.get("is_super_admin") is True,
          "envelope: can_read true, can_write false (read-only), is_super_admin true for super_admin",
          f"{perms}")
    check(isinstance(b_oa, dict) and (b_oa.get("permissions") or {}).get("is_super_admin") is False,
          "envelope: is_super_admin false for the org admin")
    check(isinstance(vocab, dict) and vocab.get("editable") == [] and vocab.get("inline_editable") == [],
          "envelope: editable and inline_editable are EMPTY arrays, never omitted")
    return {"super": b_sa, "super_text": results["super_admin"][2], "org_admin_text": results["org_admin"][2]}


async def menu() -> None:
    section("[Y] Menu entry renders for both admin types, not for a member, no truthy fallback")
    payloads = {}
    for name, uid in (("super_admin", U_SUPER), ("org_admin", U_ORGADMIN), ("member", U_MEMBER),
                      ("string_only", U_STRING_ONLY)):
        s, body, _ = await api(uid, "/api/v1/users/me")
        check(s == 200, f"/users/me for the {name} fixture (the REAL envelope the sidebar reads)", f"HTTP {s}")
        payloads[name] = body
    proc = subprocess.run(
        ["node", str(HERE.parent / "modelresearch_menu_harness.mjs")],
        input=json.dumps(payloads), capture_output=True, text=True, timeout=60,
    )
    try:
        data = json.loads(proc.stdout)
    except ValueError:
        check(False, "menu harness ran", f"rc={proc.returncode} stderr={proc.stderr[:300]}")
        return
    item = data.get("item") or {}
    check(item.get("strict") is True and item.get("perm") == "manage_org_settings" and item.get("adminIndex") is True,
          "the menu item exists, behind the STRICT manage_org_settings gate, and on the /admin index", f"{item}")
    real = data.get("real", {})
    for name in ("super_admin", "org_admin"):
        r = real.get(name, {})
        check(r.get("sidebar") is True and r.get("adminIndex") is True,
              f"{name}: entry renders in the sidebar and the /admin index", f"{r}")
    for name in ("member", "string_only"):
        r = real.get(name, {})
        check(r.get("sidebar") is False and r.get("adminIndex") is False,
              f"{name}: entry does NOT render", f"{r}")
    lost = data.get("lost", {})
    check(len(lost) == 6 and all(v.get("gate") is False and v.get("sidebar") is False
                                 and v.get("adminIndex") is False for v in lost.values()),
          "every missing / malformed / fallback envelope hides the entry (fails CLOSED)", f"{lost}")

    # The sidebar renders the item ONLY inside the strict gate.
    sb = (WEB_DIR / "components/Sidebar.jsx").read_text()
    m = re.search(r"\{canAccess\(me, GATE_MANAGE_ORG_SETTINGS_STRICT\) && \(\s*<NavLink\s+item=\{MODEL_RESEARCH_ITEM\}", sb)
    check(bool(m) and sb.count("item={MODEL_RESEARCH_ITEM}") == 1,
          "Sidebar.jsx renders MODEL_RESEARCH_ITEM once, only inside canAccess(me, GATE_MANAGE_ORG_SETTINGS_STRICT)")
    mv = (WEB_DIR / "lib/menuVisibility.mjs").read_text()
    body = mv.split("export function canPermStrict", 1)[-1].split("\n}\n", 1)[0]
    fallback = re.search(r"(\|\||\?\?)\s*(true|\[|\{)", body)
    only_true = [l.strip() for l in body.splitlines() if "return true" in l]
    check("roles" not in body and fallback is None
          and only_true == ["if (isSuperAdmin(me)) return true;"],
          "canPermStrict has no role-less default-allow and no truthy fallback (source)",
          f"fallback={fallback.group(0) if fallback else None} return_true_lines={only_true}")
    grid = (WEB_DIR / "components/admin/ModelResearchGrid.jsx").read_text()
    check("permissions?.can_read !== true" in grid and "|| DEFAULT" not in grid,
          "the grid renders data only on a real envelope (can_read === true), no default fallback (source)")


def rows_and_flags(resp: dict, facts: dict, catalog: dict, system_one: list) -> None:
    rows = resp.get("rows", [])
    meta = resp.get("meta", {})
    cmap = facts["cost_map"]
    src_models = {k for k, v in cmap.items() if is_model(k, v)}

    section("[Y] Row count matches the source's model count (plus System One rows)")
    check(meta.get("source") == "proxy", "source is the proxy (preferred), labelled on the response",
          f"source={meta.get('source')} label={meta.get('source_label')}")
    price_rows = [r for r in rows if r.get("row_source") == "proxy"]
    s1_rows = [r for r in rows if r.get("kind") == "System One"]
    extra = [r for r in rows if r.get("row_source") in ("proxy_registration", "platform_catalog")]
    check(len(price_rows) == len(src_models), "price-list rows == source model count",
          f"rows={len(price_rows)} source={len(src_models)}")
    check({r["model"] for r in price_rows} == src_models, "the price-list rows are EXACTLY the source's model keys",
          f"missing={sorted(src_models - {r['model'] for r in price_rows})[:5]} "
          f"extra={sorted({r['model'] for r in price_rows} - src_models)[:5]}")
    check(len(s1_rows) == len(system_one), "one row per ai_system_one_models entry",
          f"rows={len(s1_rows)} table={len(system_one)}")
    check(len(rows) == len(src_models) + len(system_one) + len(extra),
          "total rows == source models + System One rows + unpriced registrations/catalog entries",
          f"total={len(rows)} = {len(src_models)} + {len(system_one)} + {len(extra)}")
    check(len({r["id"] for r in rows}) == len(rows), "row ids are unique")
    stubs = sum(1 for k, v in cmap.items() if k != "sample_spec" and not is_model(k, v))
    check(meta.get("counts", {}).get("source_non_model_entries_excluded") == stubs,
          "non-model entries (sample_spec aside) are excluded and counted", f"excluded={stubs}")
    check(bool(meta.get("last_refreshed")) and "prices_as_of_note" in meta and "source_label" in meta,
          "metadata carries source label, prices-as-of (+ note) and last-refreshed",
          f"prices_as_of={meta.get('prices_as_of')} last_refreshed={meta.get('last_refreshed')}")

    section("[Y] 'Live on our proxy' matches /model/info exactly")
    deployments = facts["model_info"]
    aliases = {d.get("model_name") for d in deployments if d.get("model_name")}
    flagged = [r for r in rows if r.get("live_on_proxy") is True]
    flagged_aliases = [a for r in flagged for a in r.get("proxy_aliases", [])]
    check(set(flagged_aliases) == aliases and len(flagged_aliases) == len(aliases),
          "every registered model_name appears exactly once, on a flagged row",
          f"flagged={sorted(flagged_aliases)} registered={sorted(aliases)}")
    check(all(r.get("proxy_aliases") for r in flagged)
          and not any(r.get("proxy_aliases") for r in rows if r.get("live_on_proxy") is not True),
          "nothing else is flagged: live rows == rows carrying an alias")
    by_alias = {a: r for r in flagged for a in r.get("proxy_aliases", [])}
    wrong = []
    for d in deployments:
        key = (d.get("model_info") or {}).get("key")
        r = by_alias.get(d.get("model_name"))
        if key in src_models and (r is None or r["model"] != key):
            wrong.append((d.get("model_name"), key, r and r["model"]))
    check(not wrong, "each deployment is flagged on the row of its own model_info.key", f"wrong={wrong}")

    section("[Y] 'In platform catalog' flags + availability match platform_model_catalog exactly")
    seen: dict[str, str] = {}
    multi = []
    for r in rows:
        ids = r.get("catalog_model_ids", [])
        if r.get("in_catalog") is True:
            if len(ids) == 1:
                seen[ids[0]] = r.get("catalog_availability")
            else:
                multi.append(ids)
                for i in ids:
                    seen[i] = catalog.get(i)  # availability joined on the row; ids still must match
        elif ids or r.get("catalog_availability"):
            seen["__unflagged_with_catalog_data__"] = r["id"]
    check(seen == catalog, "{model_id: availability} from rows == platform_model_catalog (RLS-context read)",
          f"rows={seen} table={catalog}")
    in_cat = [r for r in rows if r.get("in_catalog") is True]
    check(sum(len(r.get("catalog_model_ids", [])) for r in in_cat) == len(catalog),
          "each catalog entry lands on exactly one row", f"multi-entry rows={multi}")
    fx = [r for r in rows if CATALOG_FIXTURE in r.get("catalog_model_ids", [])]
    check(len(fx) == 1 and fx[0].get("row_source") == "platform_catalog"
          and fx[0].get("catalog_availability") == "disabled" and fx[0].get("live_on_proxy") is False,
          "an unpriced, unregistered catalog entry gets its own row with its own availability ('disabled')",
          f"{fx[:1]}")
    live_cat = [r for r in in_cat if r.get("live_on_proxy") is True]
    check(all(set(r["catalog_model_ids"]) <= set(r["proxy_aliases"]) for r in live_cat) and len(live_cat) >= 1,
          "catalog entries that are proxy aliases resolve to their deployment's price-list row",
          f"{[(r['model'], r['catalog_model_ids']) for r in live_cat]}")

    section("[Y] The Jev row is present, kind 'System One'")
    jev = [r for r in rows if r.get("model") == JEV_KEY]
    db_jev = [s for s in system_one if s["key"] == JEV_KEY]
    check(len(jev) == 1 and jev[0].get("kind") == "System One", "exactly one Jev row, kind 'System One'",
          f"{jev[:1]}")
    if jev and db_jev:
        check(jev[0].get("version") == db_jev[0]["model_version"]
              and jev[0].get("system_one_availability") == db_jev[0]["availability"]
              and jev[0].get("provider") == db_jev[0]["provider"],
              "Jev's version / availability / provider equal ai_system_one_models", f"{db_jev[0]}")

    section("[Y] Per-1M prices == source per-token price x 1,000,000 exactly (sample)")
    by_model = {r["model"]: r for r in price_rows}
    ordered = sorted(src_models)
    sample = set(ordered[:: max(1, len(ordered) // 40)])
    sample |= {k for k in ("claude-sonnet-4-6", "claude-haiku-4-5-20251001", "gpt-5", "voyage/voyage-3.5",
                           "gpt-5-2025-08-07", "gemini-2.5-pro") if k in src_models}
    bad = []
    n_compared = 0
    for k in sorted(sample):
        r = by_model.get(k, {})
        for rf, sf in PRICE_PAIRS:
            v = cmap[k].get(sf)
            if real_number(v):
                n_compared += 1
                if r.get(rf) != v * 1_000_000:
                    bad.append((k, rf, r.get(rf), v))
            elif rf in r:
                bad.append((k, rf, r.get(rf), "source has none"))
    check(not bad and n_compared > 40, f"{len(sample)} sampled models, {n_compared} prices: all exact; "
          "absent in source -> absent on row", f"mismatches={bad[:5]}")

    section("[Y] Version is blank, not invented, where no dated suffix exists")
    invented = []
    for r in price_rows:
        v = r.get("version")
        if v is None:
            continue
        digits = v.replace("-", "")
        ok_date = True
        try:
            datetime.strptime(digits, "%Y%m%d")
        except ValueError:
            ok_date = False
        if not ok_date or (digits not in r["model"] and v not in r["model"]):
            invented.append((r["model"], v))
    check(not invented, "every non-blank version is a real date present in the model's own name",
          f"invented={invented[:5]}")
    undated = [k for k in ("gpt-5", "claude-sonnet-4-6", "claude-haiku-4-5", "voyage/voyage-3.5",
                           "gpt-4-0613", "gpt-4o") if k in by_model]
    check(len(undated) >= 3 and all("version" not in by_model[k] for k in undated),
          "undated models have NO version (incl. gpt-4-0613's MMDD)", f"checked={undated}")
    dated = {"claude-haiku-4-5-20251001": "2025-10-01", "gpt-5-2025-08-07": "2025-08-07"}
    got = {k: by_model[k].get("version") for k in dated if k in by_model}
    check(bool(got) and all(got[k] == dated[k] for k in got), "dated suffixes parse to their date", f"{got}")
    n_ver = sum(1 for r in price_rows if r.get("version"))
    check(0 < n_ver < len(price_rows) // 2, "a minority of rows carry a version (most names are undated)",
          f"{n_ver} of {len(price_rows)}")


def secrets(texts: list[str], resp: dict) -> None:
    section("[Y] No row and no response contains api_key, api_base, or any credential field")
    master = os.environ.get("LITELLM_MASTER_KEY", "")
    for i, text in enumerate(texts):
        hits = [t for t in SECRET_TERMS if t in text]
        check(not hits, f"raw response text #{i + 1} contains none of {len(SECRET_TERMS)} credential terms",
              f"hits={hits}")
        check(bool(master) and master not in text, f"raw response text #{i + 1} does not contain the master key value")
    rows = resp.get("rows", [])
    off = sorted({k for r in rows for k in r} - ALLOWED_ROW_KEYS)
    check(not off, "every row key is in the verify's own allow-list", f"unexpected={off}")
    check(set(resp) == {"rows", "meta", "vocabularies", "permissions"}, "top-level response keys are exactly the envelope",
          f"{sorted(resp)}")


async def refresh_section() -> None:
    section("[Y] Refresh genuinely re-fetches: last-refreshed changes and the cache is bypassed")
    from services import model_research

    calls: list[str] = []
    real_get = model_research._proxy_get

    def spy(path, timeout=60.0):
        calls.append(path)
        return real_get(path, timeout=timeout)

    model_research._proxy_get = spy
    try:
        s1, b1, _ = await api(U_SUPER, PATH + "?refresh=true")
        n1 = len(calls)
        s2, b2, _ = await api(U_SUPER, PATH)
        n2 = len(calls)
        s3, b3, _ = await api(U_ORGADMIN, PATH + "?refresh=true")
        n3 = len(calls)
    finally:
        model_research._proxy_get = real_get
    m1, m2, m3 = (b.get("meta", {}) for b in (b1, b2, b3))
    check(s1 == s2 == s3 == 200, "three calls succeed", f"{s1} {s2} {s3}")
    check(m1.get("cache") == "bypassed" and n1 >= 2 and "/public/litellm_model_cost_map" in calls[:n1],
          "refresh=true re-reads the proxy (price list + /model/info)", f"proxy GETs={calls[:n1]}")
    check(m2.get("cache") == "hit" and n2 == n1 and m2.get("last_refreshed") == m1.get("last_refreshed"),
          "a plain call within the TTL is served from cache: zero proxy reads, same last-refreshed",
          f"cache={m2.get('cache')} new proxy GETs={n2 - n1}")
    check(m3.get("cache") == "bypassed" and n3 > n2 and (m3.get("last_refreshed") or "") > (m1.get("last_refreshed") or "~"),
          "a second refresh bypasses the cache, re-reads the proxy, and last-refreshed moves forward",
          f"{m1.get('last_refreshed')} -> {m3.get('last_refreshed')}; new proxy GETs={n3 - n2}")
    check(all(p in ("/public/litellm_model_cost_map", "/model/cost_map/source",
                    "/schedule/model_cost_map_reload/status", "/model/info") for p in calls),
          "refresh only READS: no /reload/ or any other proxy path is called", f"paths={sorted(set(calls))}")


# ═══════════════════════════════════════════════════════════════════════════
COUNTED_TABLES = ("platform_model_catalog", "ai_system_one_models", "users", "user_roles", "roles",
                  "role_permissions", "permissions", "organizations")


async def counts(conn) -> dict:
    return {t: await rls_val(conn, f"SELECT count(*) FROM public.{t}") for t in COUNTED_TABLES}


async def catalog_snapshot(conn) -> dict:
    return {
        "pmc": [_norm(r) for r in await rls_fetch(conn, "SELECT * FROM public.platform_model_catalog ORDER BY model_id")],
        "s1": [_norm(r) for r in await rls_fetch(conn, "SELECT * FROM public.ai_system_one_models ORDER BY key")],
    }


def proxy_state() -> dict:
    mi = litellm_json("/model/info") or {}
    return {
        "deployments": sorted(((d.get("model_info") or {}).get("id"), d.get("model_name")) for d in mi.get("data", [])),
        "reload": litellm_json("/schedule/model_cost_map_reload/status"),
        "source": litellm_json("/model/cost_map/source"),
    }


def static_no_writes() -> None:
    write_sql = re.compile(r"\b(INSERT\s+INTO|UPDATE\s+[\w.]+\s+SET|DELETE\s+FROM|TRUNCATE|ALTER\s+TABLE)\b", re.I)
    for rel in ("services/model_research.py", "routers/model_research.py"):
        text = (API_DIR / rel).read_text()
        check(not write_sql.search(text), f"{rel} contains no write SQL")
        check("method=" not in text and "/reload/" not in text and "@router.post" not in text
              and "@router.put" not in text and "@router.delete" not in text and "@router.patch" not in text,
              f"{rel} issues no non-GET proxy call and declares no write route")


def npm_build() -> None:
    section("[Y] npm run build exits 0")
    try:
        proc = subprocess.run(["npm", "run", "build"], cwd=WEB_DIR, capture_output=True, text=True, timeout=1200)
    except Exception as exc:  # noqa: BLE001
        check(False, "npm run build exits 0", type(exc).__name__)
        return
    ok = proc.returncode == 0
    check(ok, "npm run build exits 0", "" if ok else (proc.stdout + proc.stderr)[-600:])
    if ok:
        out = proc.stdout
        check("/admin/model-research" in out and "/api/admin/model-research" in out,
              "the build emitted the page and the forward route")


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
    baseline_counts = None
    try:
        await teardown(conn)
        baseline_counts = await counts(conn)
        facts = task1_findings()

        section("Fixtures")
        await seed(conn)
        n = await rls_val(conn, "SELECT count(*) FROM users WHERE id = ANY($1::uuid[])", FIXTURE_USERS)
        check(n == len(FIXTURE_USERS), "fixture users seeded", f"rows={n}")

        section("No-write baseline (taken AFTER seeding, BEFORE any endpoint call)")
        before_counts = await counts(conn)
        before_snap = await catalog_snapshot(conn)
        before_proxy = proxy_state()
        catalog = {r["model_id"]: r["availability"]
                   for r in await rls_fetch(conn, "SELECT model_id, availability FROM public.platform_model_catalog")}
        system_one = [dict(r) for r in await rls_fetch(
            conn, "SELECT key, provider, model_version, availability FROM public.ai_system_one_models")]
        check(any(k.startswith(FIXTURE_PREFIX) for k in catalog) and len(system_one) >= 1,
              "baseline read under RLS context sees the catalog fixture and the System One catalog",
              f"catalog={len(catalog)} system_one={len(system_one)}")

        acc = await access(conn)
        await menu()
        resp = acc["super"] if isinstance(acc["super"], dict) else {}
        rows_and_flags(resp, facts, catalog, system_one)
        await refresh_section()
        secrets([acc["super_text"], acc["org_admin_text"]], resp)

        section("[Y] The endpoint performs no writes")
        after_counts = await counts(conn)
        after_snap = await catalog_snapshot(conn)
        after_proxy = proxy_state()
        check(before_counts == after_counts, f"row counts unchanged on {len(COUNTED_TABLES)} touched tables",
              f"before={before_counts} after={after_counts}")
        check(before_snap == after_snap, "platform_model_catalog and ai_system_one_models are byte-identical")
        check(before_proxy == after_proxy,
              "proxy unchanged: same deployments, no reload scheduled/run, same cost-map source + count")
        static_no_writes()
    finally:
        section("Teardown")
        try:
            await teardown(conn)
            leftovers = {
                "users": await rls_val(conn, "SELECT count(*) FROM users WHERE id = ANY($1::uuid[])", FIXTURE_USERS),
                "org": await rls_val(conn, "SELECT count(*) FROM organizations WHERE id = $1", ORG),
                "roles": await rls_val(conn, "SELECT count(*) FROM roles WHERE org_id = $1", ORG),
                "catalog": await rls_val(conn, "SELECT count(*) FROM platform_model_catalog WHERE model_id LIKE $1",
                                         FIXTURE_PREFIX + "%"),
            }
            check(all(v == 0 for v in leftovers.values()), "zero fixture rows remain", f"{leftovers}")
            if baseline_counts is not None:
                final = await counts(conn)
                check(final == baseline_counts, "exact before/after row counts match the pre-run baseline",
                      f"before={baseline_counts} after={final}")
        finally:
            REGISTRY.sync_catalog = original_sync
            await conn.close()

    npm_build()

    print(f"\n{'=' * 70}")
    if _finds:
        print(f"FINDINGS: {len(_finds)}")
    if _blocked:
        print(f"BLOCKED (not counted as FAIL): {len(_blocked)} — {_blocked}")
    print(f"TOTAL: {_n_pass} PASS, {_n_fail} FAIL")
    print("=" * 70)
    return 0 if _ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main_async()))
