"""verify_bulkfetchui2.py — BULKFETCH UI 2: the "Run on your laptop" dialog and the
EDGAR_BULK_LAPTOP_WORKER_COMMAND setting behind it.

WRITTEN BY THE SPRINT. THE OPERATOR RUNS IT:

    python3 apps/api/scripts/verify_bulkfetchui2.py

Pass/fail only, no prompts. Prints 'TOTAL: N PASS, M FAIL' and exits non-zero on
any failure. Hydrates secrets from Doppler over HTTPS (_db_bootstrap).

BEFORE RUNNING: stop any real laptop worker (`tmux attach -t bulkworker`,
Ctrl-C). A real worker could claim a fixture run, and its heartbeat would make
"no worker online" untestable. The script checks this first and skips the queue
sections (as a FAIL) if one is live.

NO NETWORK. The SEC is never reached: nothing here downloads, and no worker runs.
R2 is a dict behind ``services.storage`` (a real S3 client RAISES). The Render
API is a MockTransport behind ``edgar_bulk_queue.render_http`` that records
every method. Every model entry point is a recorder that raises. Real spend: $0.
No process is spawned except the node tests, `npm run build`, and (after
teardown) the one flat regression.

THE SETTING. ``EDGAR_BULK_LAPTOP_WORKER_COMMAND`` is monkeypatched in this
process's environment only: unset, blank, then a sentinel. Its Doppler value is
saved first and restored at the end. If Doppler HAS a value, that is reported as
a [FIND], and the "unset" checks still run with it removed.

FIXTURES — tagged, torn down at the START and at the END:
  * structured_note_issuers rows in issuer groups 'VERIFY BFUI2 …' with 9-digit
    CIKs starting 99093 (asserted unused by any non-fixture row first);
  * edgar_pipeline_runs requested by a fixture user, or whose params.banks name
    a fixture group (each one cancelled the moment it is created, so a real
    worker started mid-run finds nothing claimable);
  * edgar_fetch_workers rows 'verify-bfui2:…' (the mocked fresh heartbeat);
  * a fixture org, a super-admin and an org-admin user (auth0_sub = the literal
    sub the mocked token carries; teardown finds them by id, sub and email).
Every id is a full UUID or a counter — never a slice of a UUID.

NEVER TOUCHED: real note_gold_values (count + checksum before/after), real
edgar_index_filings rows (count before/after; nothing here writes the table).

Teardown is FK child-before-parent from pg_constraint (contype::text), then every
touched table is re-read for its exact pre-test count. The regression,
verify_bulkfetchui.py, runs ONCE as a flat subprocess (1200 s timeout), only
after this script's own teardown left zero fixture rows, and only while no real
worker is heartbeating.
"""
from __future__ import annotations

import os as _authz_os
_authz_os.environ["AUTHZ_TEST_MODE"] = "1"  # AUTHZ1: in-process test identity only; set before main is imported

import asyncio
import json
import os
import pathlib
import re
import subprocess
import sys
from uuid import UUID

HERE = pathlib.Path(__file__).resolve()
sys.path.insert(0, str(HERE.parent))
from _db_bootstrap import bootstrap_async  # noqa: E402  (also puts apps/api on sys.path)

import asyncpg  # noqa: E402
import httpx  # noqa: E402

REPO = HERE.parents[3]
API_DIR = HERE.parents[1]
WEB_DIR = REPO / "apps" / "web"
DISCOVERY_DOC = REPO / "docs" / "BULKFETCHUI2_DISCOVERY.md"
STATUS_DOC = REPO / "docs" / "PROJECT_STATUS.md"
BULK_JSX = WEB_DIR / "components" / "admin" / "EdgarBulkDownload.jsx"
DIALOG_JSX = WEB_DIR / "components" / "admin" / "LaptopWorkerDialog.jsx"
BULK_LOGIC = WEB_DIR / "lib" / "edgarBulkDownload.mjs"
NODE_TEST = "tests/edgar/laptopDialog.test.mjs"

# The command that works on Joe's laptop (CONFIRMED FACTS, 2026-10-20) — the
# expected default, written out here independently of the module's constant.
EXPECTED_DEFAULT = ("cd /mnt/c/Users/Joe/2ndActCapital && doppler run -p hollisworks -c prd -- "
                    "venv/bin/python -u apps/api/scripts/run_bulk_fetch.py --worker --target laptop")
SETTING = "EDGAR_BULK_LAPTOP_WORKER_COMMAND"
SENTINEL_COMMAND = "cd /tmp/verify-bfui2 && echo verify-bfui2-sentinel --worker --target laptop"

TAG = "bulkfetchui2-verify"
GROUP_PREFIX = "VERIFY BFUI2 "
CIK_PREFIX = "99093"
WORKER_PREFIX = "verify-bfui2:"
FIXTURE_HOST = "verify-bfui2-host"
ORG = UUID("99000000-0000-0000-0000-00000b0f2201")
U_SUPER = UUID("99000000-0000-0000-0000-00000b0f2211")
U_ORGADMIN = UUID("99000000-0000-0000-0000-00000b0f2212")
FIXTURE_USERS = [U_SUPER, U_ORGADMIN]
FIXTURE_EMAIL_LIKE = "bulkfetchui2_%@test.local"
HEADERS = {"Authorization": "Bearer verify-token"}
BASE = "/api/v1/admin/edgar/bulk"
GROUP = GROUP_PREFIX + "Alpha"
QUEUE_BODY = {"params": {"banks": [GROUP], "from": "2025-01-01", "to": "2025-12-31", "kinds": ["final"]}}

_n_pass = 0
_n_fail = 0


def check(passed: bool, label: str, detail: str = "") -> bool:
    assert isinstance(passed, bool), (
        f"check() received passed={passed!r} ({type(passed).__name__}), not a bool, for {label!r}")
    global _n_pass, _n_fail
    print(f"{'[PASS]' if passed else '[FAIL]'} {label}" + (f"  — {detail}" if detail else ""))
    if passed:
        _n_pass += 1
    else:
        _n_fail += 1
    return passed


def find(label: str, detail: str = "") -> None:
    print(f"[FIND] {label}" + (f"  — {detail}" if detail else ""))


def section(title: str) -> None:
    print(f"\n── {title} ──")


def _sub(uid: UUID) -> str:
    return f"bulkfetchui2_{uid.hex}"


# ═══ RLS-scoped DB helpers (every read sets the platform RLS context) ═══════
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


async def rls_exec(conn, q, *a):
    async with conn.transaction():
        await _super(conn)
        return await conn.execute(q, *a)


async def check_defs(conn, table: str) -> dict[str, str]:
    rows = await conn.fetch(
        "SELECT conname, pg_get_constraintdef(oid) AS def FROM pg_constraint "
        "WHERE conrelid = $1::regclass AND contype::text = 'c'", table)
    return {r["conname"]: r["def"] for r in rows}


def allowed(defs: dict[str, str], column: str) -> set[str]:
    out: set[str] = set()
    for d in defs.values():
        if re.search(rf"\(\(?{column}\b", d) and ("ARRAY[" in d or " IN " in d):
            out |= set(re.findall(r"'([^']+)'::text", d))
    return out


_cik_n = 0


def next_cik() -> str:
    global _cik_n
    _cik_n += 1
    return f"{CIK_PREFIX}{_cik_n:04d}"


# ═══ Teardown — FK order from pg_constraint; counts as the backstop ═══════
TOUCHED = (
    "portfolio.edgar_fetch_workers", "portfolio.edgar_pipeline_lease", "portfolio.edgar_pipeline_runs",
    "portfolio.structured_note_issuers",
)
COUNTED = TOUCHED + ("public.users", "public.organizations", "public.roles", "public.user_roles",
                     "portfolio.edgar_index_filings", "portfolio.note_gold_values")

_RUN_FIXTURE_SQL = """
    SELECT id FROM portfolio.edgar_pipeline_runs r
     WHERE r.requested_by = ANY($1::uuid[])
        OR r.worker_id LIKE $2
        OR (jsonb_typeof(r.params->'banks') = 'array'
            AND EXISTS (SELECT 1 FROM jsonb_array_elements_text(r.params->'banks') b WHERE b LIKE $3))
"""


async def fixture_keys(conn) -> dict:
    runs = await rls_fetch(conn, _RUN_FIXTURE_SQL, FIXTURE_USERS, WORKER_PREFIX + "%", GROUP_PREFIX + "%")
    return {"runs": [r["id"] for r in runs]}


def predicates(k: dict) -> dict[str, tuple[str, list]]:
    runs = k["runs"]
    return {
        "portfolio.edgar_fetch_workers": ("worker_id LIKE $1 OR current_run_id = ANY($2::uuid[])",
                                          [WORKER_PREFIX + "%", runs]),
        "portfolio.edgar_pipeline_lease": ("lease_name = ANY($1::text[])", [[f"bulk_fetch:{r}" for r in runs]]),
        "portfolio.edgar_pipeline_runs": ("id = ANY($1::uuid[])", [runs]),
        "portfolio.structured_note_issuers": ("issuer_group LIKE $1", [GROUP_PREFIX + "%"]),
    }


async def delete_order(conn) -> list[str]:
    rows = await conn.fetch(
        """SELECT n1.nspname || '.' || c1.relname AS child, n2.nspname || '.' || c2.relname AS parent
             FROM pg_constraint k
             JOIN pg_class c1 ON c1.oid = k.conrelid JOIN pg_namespace n1 ON n1.oid = c1.relnamespace
             JOIN pg_class c2 ON c2.oid = k.confrelid JOIN pg_namespace n2 ON n2.oid = c2.relnamespace
            WHERE k.contype::text = 'f'""")
    tables = set(TOUCHED)
    children: dict[str, set[str]] = {t: set() for t in tables}
    for r in rows:
        if r["child"] in tables and r["parent"] in tables and r["child"] != r["parent"]:
            children[r["parent"]].add(r["child"])
    order, remaining = [], set(tables)
    while remaining:
        ready = sorted(t for t in remaining if not (children[t] & remaining))
        if not ready:
            raise RuntimeError(f"FK cycle among {sorted(remaining)}")
        order += ready
        remaining -= set(ready)
    return order


async def fixture_counts(conn) -> dict[str, int]:
    k = await fixture_keys(conn)
    out = {t: await rls_val(conn, f"SELECT count(*) FROM {t} WHERE {w}", *a) for t, (w, a) in predicates(k).items()}
    out["public.users"] = await rls_val(
        conn, "SELECT count(*) FROM users WHERE id = ANY($1::uuid[]) OR auth0_sub = ANY($2::text[]) OR email LIKE $3",
        FIXTURE_USERS, [_sub(u) for u in FIXTURE_USERS], FIXTURE_EMAIL_LIKE)
    out["public.organizations"] = await rls_val(conn, "SELECT count(*) FROM organizations WHERE id = $1", ORG)
    out["public.roles"] = await rls_val(conn, "SELECT count(*) FROM roles WHERE org_id = $1", ORG)
    return out


async def teardown(conn) -> None:
    k = await fixture_keys(conn)
    preds = predicates(k)
    for t in await delete_order(conn):
        where, args = preds[t]
        await rls_exec(conn, f"DELETE FROM {t} WHERE {where}", *args)
    rows = await rls_fetch(
        conn, "SELECT id FROM users WHERE id = ANY($1::uuid[]) OR auth0_sub = ANY($2::text[]) OR email LIKE $3",
        FIXTURE_USERS, [_sub(u) for u in FIXTURE_USERS], FIXTURE_EMAIL_LIKE)
    uids = list({r["id"] for r in rows} | set(FIXTURE_USERS))
    async with conn.transaction():
        await _super(conn)
        await conn.execute("DELETE FROM audit_log WHERE org_id = $1 OR user_id = ANY($2::uuid[])", ORG, uids)
        await conn.execute("DELETE FROM assistant_activities WHERE org_id = $1 OR user_id = ANY($2::uuid[])", ORG, uids)
        await conn.execute("DELETE FROM agent_proposals WHERE org_id = $1 OR proposed_by = ANY($2::uuid[])", ORG, uids)
        await conn.execute("DELETE FROM user_roles WHERE user_id = ANY($1::uuid[]) "
                           "OR role_id IN (SELECT id FROM roles WHERE org_id = $2)", uids, ORG)
        await conn.execute("DELETE FROM role_permissions WHERE role_id IN (SELECT id FROM roles WHERE org_id = $1)", ORG)
        await conn.execute("DELETE FROM roles WHERE org_id = $1", ORG)
        await conn.execute("DELETE FROM users WHERE id = ANY($1::uuid[])", uids)
        await conn.execute("DELETE FROM organizations WHERE id = $1", ORG)


async def table_counts(conn) -> dict[str, int]:
    return {t: await rls_val(conn, f"SELECT count(*) FROM {t}") for t in COUNTED}


async def gold_checksum(conn) -> tuple[int, str]:
    r = await rls_row(
        conn, """SELECT count(*) AS n,
                        md5(COALESCE(string_agg(id::text || field_key || COALESCE(value::text, '') || action
                                                || COALESCE(valid_to::text, '') || metadata::text, ',' ORDER BY id), '')) AS h
                   FROM portfolio.note_gold_values""")
    return int(r["n"]), r["h"]


# ═══ Mocks: R2, Render, models (the SEC is never reached: nothing downloads) ═
R2: dict[str, bytes] = {}
MODEL_CALLS: list[str] = []
RENDER_CALLS: list[tuple[str, str]] = []


def render_handler(request: httpx.Request) -> httpx.Response:
    RENDER_CALLS.append((request.method, request.url.path))
    return httpx.Response(404 if request.method == "GET" else 405, json={"message": "not found"})


def install_mocks():
    from services import edgar_bulk_queue, edgar_fetch, extraction, storage
    from services.note_extraction import proxy

    def fake_upload(key, data, content_type=None, bucket=None):
        R2[key] = bytes(data)
        return key

    def no_s3(*_a, **_k):
        raise AssertionError("a real R2 client was requested during verify_bulkfetchui2")

    storage.upload_bytes = fake_upload
    storage.download_bytes = lambda key, bucket=None: R2[key]
    storage.get_s3_client = no_s3

    def recorder(name):
        async def _no(*_a, **_k):
            MODEL_CALLS.append(name)
            raise AssertionError(f"{name} must never be called by bulkfetchui2")
        return _no

    proxy.chat = recorder("proxy.chat")
    proxy.jev = recorder("proxy.jev")
    for fn in ("call_claude_json", "call_claude_text", "call_claude_with_tools"):
        setattr(extraction, fn, recorder(f"extraction.{fn}"))

    edgar_bulk_queue.render_http = lambda: httpx.AsyncClient(transport=httpx.MockTransport(render_handler))

    def no_sec(*_a, **_k):
        raise AssertionError("an SEC client was requested during verify_bulkfetchui2")

    if hasattr(edgar_fetch, "make_client"):
        edgar_fetch.make_client = no_sec
    os.environ.setdefault("EDGAR_USER_AGENT", "Hollisworks verify_bulkfetchui2 (verify@test.local)")
    os.environ.pop(edgar_bulk_queue.RENDER_WORKER_ENV, None)    # Render "not set up" throughout
    edgar_bulk_queue._RENDER_CACHE.clear()


# ═══ API through the real app (mocked token) ═══════════════════════════════
async def api(uid, method, path, body=None):
    import main
    sub = _sub(uid)
    main.verify_token = lambda _t: {"sub": sub, "email": f"{sub}@test.local", "org_id": str(ORG)}
    kw = {"headers": HEADERS}
    if body is not None:
        kw["json"] = body
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app, raise_app_exceptions=False),
                                 base_url="http://verify") as client:
        res = await client.request(method, path, **kw)
    try:
        parsed = json.loads(res.text)
    except ValueError:
        parsed = {"raw": res.text[:300]}
    return res.status_code, parsed


def detail(body) -> dict:
    d = (body or {}).get("detail")
    return d if isinstance(d, dict) else {"message": d}


async def run_row(url: str, rid) -> dict:
    """INDEPENDENT re-read: a fresh connection, RLS context set."""
    c = await asyncpg.connect(url, statement_cache_size=0)
    try:
        r = await rls_row(c, "SELECT * FROM portfolio.edgar_pipeline_runs WHERE id = $1", rid)
        return dict(r) if r else {}
    finally:
        await c.close()


async def cancel_new(conn, q, body) -> None:
    """Every run this script queues is cancelled at once — never left for a real worker."""
    rid = ((body or {}).get("run") or {}).get("id")
    if rid:
        await q.cancel(conn, UUID(rid))


async def seed(conn) -> None:
    from services.rbac import grant_org_admin
    roles_ok = allowed(await check_defs(conn, "users"), "role")
    check(not roles_ok or {"super_admin", "member"} <= roles_ok, "fixture users.role values satisfy the live CHECK",
          f"{sorted(roles_ok)}")
    w_ok = allowed(await check_defs(conn, "portfolio.edgar_fetch_workers"), "target")
    check("laptop" in w_ok, "fixture edgar_fetch_workers.target 'laptop' is a live CHECK value", f"{sorted(w_ok)}")
    inc_ok = allowed(await check_defs(conn, "portfolio.structured_note_issuers"), "include_status")
    role_ok = allowed(await check_defs(conn, "portfolio.structured_note_issuers"), "filer_role")
    check((not inc_ok or "yes" in inc_ok) and (not role_ok or "issuer" in role_ok),
          "fixture structured_note_issuers values (include_status 'yes', filer_role 'issuer') satisfy the live CHECKs",
          f"{sorted(inc_ok)} {sorted(role_ok)}")
    clash = await rls_val(conn, "SELECT count(*) FROM portfolio.structured_note_issuers "
                                "WHERE filer_cik LIKE $1 AND issuer_group NOT LIKE $2", CIK_PREFIX + "%",
                          GROUP_PREFIX + "%")
    check(clash == 0, f"no real issuer uses a {CIK_PREFIX}… CIK (the fixture CIK space is free)", f"{clash}")
    cik = next_cik()
    async with conn.transaction():
        await _super(conn)
        await conn.execute("INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3)",
                           ORG, "BULKFETCHUI2 Verify Fixture Org", f"{ORG}-bulkfetchui2")
        for uid, role in ((U_SUPER, "super_admin"), (U_ORGADMIN, "member")):
            sub = _sub(uid)
            await conn.execute(
                """INSERT INTO users (id, org_id, email, full_name, auth0_sub, role, is_active)
                   VALUES ($1, $2, $3, $4, $5, $6, true)""", uid, ORG, f"{sub}@test.local", sub, sub, role)
        await conn.execute(
            """INSERT INTO portfolio.structured_note_issuers
                   (filer_cik, issuer_group, filer_name, filer_role, credit_entity, include_status, notes)
               VALUES ($1, $2, $3, 'issuer', $4, 'yes', $5)""",
            cik, GROUP, f"{GROUP} Filer 1", f"{GROUP} Credit", TAG)
        await grant_org_admin(conn, U_ORGADMIN, ORG)


# ═══ Sections ══════════════════════════════════════════════════════════════
def y_task1() -> None:
    section("[Y] Task 1 — the discovery findings are reported")
    text = DISCOVERY_DOC.read_text(encoding="utf-8") if DISCOVERY_DOC.exists() else ""
    heads = re.findall(r"^## (1[a-z])\.", text, re.M)
    check(heads == ["1a", "1b"], "docs/BULKFETCHUI2_DISCOVERY.md reports 1a and 1b in order", f"{heads}")
    for label, pat in (
        ("1a names the queue confirm, the response handler and the worker fetch",
         r"ConfirmDialog.*queue\(\).*queueOutcome.*loadBulkWorkersAction"),
        ("1b names where the start command is built and the (absent) dialog component",
         r"LAPTOP_COMMAND.*workers_state.*_check_target.*(?:None to reuse|no .*dialog)"),
    ):
        check(re.search(pat, text, re.S | re.I) is not None, label)


async def y_setting(conn, q) -> None:
    section("[Y] Setting — laptop_start_command is the working default when unset, the setting's value when set")
    check(q.LAPTOP_COMMAND == EXPECTED_DEFAULT and q.LAPTOP_COMMAND_ENV == SETTING,
          "the module's default and setting name are the confirmed working command and EDGAR_BULK_LAPTOP_WORKER_COMMAND",
          q.LAPTOP_COMMAND)
    try:
        for label, value in (("unset", None), ("blank", "   ")):
            if value is None:
                os.environ.pop(SETTING, None)
            else:
                os.environ[SETTING] = value
            c1, w = await api(U_SUPER, "GET", f"{BASE}/workers")
            check(c1 == 200 and w.get("laptop_start_command") == EXPECTED_DEFAULT
                  and (w.get("laptop") or {}).get("start_command") == EXPECTED_DEFAULT,
                  f"setting {label}: GET workers returns the default working command",
                  f"HTTP {c1}: {w.get('laptop_start_command')!r}")
            c2, r = await api(U_SUPER, "POST", f"{BASE}/runs", QUEUE_BODY)
            await cancel_new(conn, q, r)
            check(c2 == 202 and r.get("laptop_start_command") == EXPECTED_DEFAULT,
                  f"setting {label}: POST runs returns the default working command",
                  f"HTTP {c2}: {r.get('laptop_start_command')!r}")
        os.environ[SETTING] = f"  {SENTINEL_COMMAND}  "
        c1, w = await api(U_SUPER, "GET", f"{BASE}/workers")
        start = [s for s in (w.get("laptop_instructions") or []) if s.get("key") == "start_worker"]
        check(c1 == 200 and w.get("laptop_start_command") == SENTINEL_COMMAND
              and (w.get("laptop") or {}).get("start_command") == SENTINEL_COMMAND,
              "setting set (monkeypatched): GET workers returns the setting's value, trimmed",
              f"{w.get('laptop_start_command')!r}")
        check(len(start) == 1 and start[0].get("command") == SENTINEL_COMMAND
              and EXPECTED_DEFAULT not in json.dumps(w.get("laptop_instructions")),
              "setting set: the instructions' start step carries the setting's value, and the default appears nowhere",
              f"{start[0].get('command') if start else None!r}")
        c2, r = await api(U_SUPER, "POST", f"{BASE}/runs", QUEUE_BODY)
        await cancel_new(conn, q, r)
        check(c2 == 202 and r.get("laptop_start_command") == SENTINEL_COMMAND
              and r.get("start_command") in (SENTINEL_COMMAND, None),
              "setting set: POST runs returns the setting's value", f"HTTP {c2}: {r.get('laptop_start_command')!r}")
    finally:
        os.environ.pop(SETTING, None)


async def y_instructions(q) -> None:
    section("[Y] laptop_instructions — a non-empty ordered list with every required step")
    os.environ.pop(SETTING, None)
    code, w = await api(U_SUPER, "GET", f"{BASE}/workers")
    steps = w.get("laptop_instructions")
    ok_list = isinstance(steps, list) and len(steps) > 0
    check(code == 200 and ok_list, "GET workers returns a non-empty laptop_instructions list",
          f"HTTP {code}, {len(steps) if isinstance(steps, list) else type(steps).__name__}")
    if not ok_list:
        return
    check([s.get("n") for s in steps] == list(range(1, len(steps) + 1)),
          "steps are numbered 1..N in list order", f"{[s.get('n') for s in steps]}")
    check(all(isinstance(s.get("title"), str) and s["title"] and isinstance(s.get("detail"), str) and s["detail"]
              and s.get("section") in ("start", "ongoing") for s in steps),
          "every step has a title, a detail and a section of start|ongoing")

    def idx(pred) -> int:
        return next((i for i, s in enumerate(steps) if pred(s)), -1)

    blob = lambda s: " ".join(str(s.get(k) or "") for k in ("title", "detail", "command", "keys"))  # noqa: E731
    want = {
        "open the Ubuntu/WSL terminal": idx(lambda s: re.search(r"Ubuntu|WSL", blob(s)) and not s.get("command")),
        "tmux new -s bulkworker": idx(lambda s: s.get("command") == "tmux new -s bulkworker"),
        "the start command": idx(lambda s: s.get("command") == EXPECTED_DEFAULT),
        "detach with Ctrl-B then D": idx(lambda s: re.search(r"Ctrl-B,? then D", blob(s)) and s.get("key") == "detach"),
        "keep the laptop awake": idx(lambda s: re.search(r"awake", blob(s)) and re.search(r"Sleep", blob(s))
                                     and re.search(r"Never", blob(s))),
        "tmux attach -t bulkworker": idx(lambda s: s.get("command") == "tmux attach -t bulkworker"),
        "stop with Ctrl-C, before verify scripts": idx(lambda s: re.search(r"Ctrl-C", blob(s))
                                                       and re.search(r"verify", blob(s), re.I)),
        "a stopped run can be requeued": idx(lambda s: re.search(r"[Rr]equeue", blob(s))
                                             and re.search(r"where it stopped", blob(s))),
    }
    for name, i in want.items():
        check(i >= 0, f"a step: {name}", f"index {i}")
    order = [want[k] for k in ("open the Ubuntu/WSL terminal", "tmux new -s bulkworker", "the start command",
                               "detach with Ctrl-B then D")]
    check(all(i >= 0 for i in order) and order == sorted(order),
          "the start steps are in order: terminal, tmux new, the command, detach", f"{order}")
    starts = [s["key"] for s in steps if s.get("section") == "start"]
    check(want["tmux attach -t bulkworker"] >= 0
          and steps[want["tmux attach -t bulkworker"]].get("section") == "ongoing"
          and steps[want["keep the laptop awake"]].get("section") == "ongoing"
          and steps[want["the start command"]].get("section") == "start",
          "the check / keep-awake steps are 'ongoing' (stay visible); the start command is a 'start' step",
          f"start steps {starts}")
    check(q.laptop_instructions() == steps, "the API returns exactly services.edgar_bulk_queue.laptop_instructions()")


async def y_queue(conn, url, q) -> None:
    section("[Y] Queue on Laptop — no worker: queued + worker_online false; fresh heartbeat: online + host")
    os.environ.pop(SETTING, None)
    code, res = await api(U_SUPER, "POST", f"{BASE}/runs", QUEUE_BODY)
    run = (res or {}).get("run") or {}
    row = await run_row(url, UUID(run["id"])) if run.get("id") else {}
    check(code == 202 and row.get("status") == "queued" and row.get("target") == "laptop"
          and row.get("run_kind") == "bulk_fetch",
          "no worker: an independent re-read shows the run queued on the laptop",
          f"HTTP {code}; row {row.get('status')} {row.get('target')}")
    check(res.get("worker_online") is False and res.get("worker_host") is None
          and res.get("start_command") == EXPECTED_DEFAULT,
          "no worker: worker_online false, no host, the start command returned",
          f"{res.get('worker_online')} {res.get('worker_host')!r}")
    await cancel_new(conn, q, res)

    wid = f"{WORKER_PREFIX}heartbeat-1"
    await rls_exec(conn, """INSERT INTO portfolio.edgar_fetch_workers (worker_id, target, host, pid, details)
                            VALUES ($1, 'laptop', $2, 1, $3::jsonb)""", wid, FIXTURE_HOST, json.dumps({"tag": TAG}))
    hb = await rls_row(conn, "SELECT host, extract(epoch FROM now() - heartbeat_at)::float AS age "
                             "FROM portfolio.edgar_fetch_workers WHERE worker_id = $1", wid)
    check(hb is not None and hb["host"] == FIXTURE_HOST and hb["age"] < q.WORKER_LOST_SECONDS,
          "the mocked heartbeat row is fresh (independent read)", f"{dict(hb) if hb else None}")
    code, res = await api(U_SUPER, "POST", f"{BASE}/runs", QUEUE_BODY)
    run = (res or {}).get("run") or {}
    row = await run_row(url, UUID(run["id"])) if run.get("id") else {}
    await cancel_new(conn, q, res)
    check(code == 202 and row.get("status") in ("queued", "cancelled") and row.get("target") == "laptop",
          "fresh heartbeat: the run was queued (re-read; cancelled by the script straight after)",
          f"HTTP {code}; row {row.get('status')}")
    check(res.get("worker_online") is True and res.get("worker_host") == FIXTURE_HOST
          and (res.get("worker") or {}).get("host") == FIXTURE_HOST and res.get("start_command") is None,
          "fresh heartbeat: worker_online true and the host returned",
          f"{res.get('worker_online')} {res.get('worker_host')!r}")
    code, w = await api(U_SUPER, "GET", f"{BASE}/workers")
    lap = (w or {}).get("laptop") or {}
    check(code == 200 and lap.get("online") is True and (lap.get("worker") or {}).get("host") == FIXTURE_HOST,
          "fresh heartbeat: GET workers shows the laptop online on that host (the dialog's green state)",
          f"{lap.get('online')} {(lap.get('worker') or {}).get('host')!r}")
    await rls_exec(conn, "UPDATE portfolio.edgar_fetch_workers SET heartbeat_at = now() - interval '1 hour' "
                         "WHERE worker_id = $1", wid)
    code, w = await api(U_SUPER, "GET", f"{BASE}/workers")
    check(code == 200 and ((w or {}).get("laptop") or {}).get("online") is False,
          "a stale heartbeat (1 h old) reads offline again (the dialog's amber state)")
    await rls_exec(conn, "DELETE FROM portfolio.edgar_fetch_workers WHERE worker_id = $1", wid)


async def y_render(conn, q) -> None:
    section("[Y] Render — still refused 409 with nothing queued (count before/after)")
    os.environ.pop(q.RENDER_WORKER_ENV, None)
    q._RENDER_CACHE.clear()
    count_q = "SELECT count(*) FROM portfolio.edgar_pipeline_runs WHERE run_kind = ANY($1::text[])"
    kinds = list(q.QUEUED_KINDS)
    n0 = await rls_val(conn, count_q, kinds)
    code, res = await api(U_SUPER, "POST", f"{BASE}/runs", {**QUEUE_BODY, "target": "render"})
    n1 = await rls_val(conn, count_q, kinds)
    d = detail(res)
    check(code == 409 and d.get("message") == q.RENDER_REFUSAL and d.get("setup_anchor") == q.SETUP_ANCHOR,
          "Render: 409 with the refusal message and setup anchor", f"HTTP {code}: {d.get('message')}")
    check(n0 == n1, "Render: zero runs queued", f"{n0} -> {n1}")


async def y_permissions(conn, q) -> None:
    section("[Y] Permission gate unchanged — org admin refused, super admin admitted, identical request")
    from services.rbac import has_org_admin_grant
    async with conn.transaction():
        await _super(conn)
        granted = await has_org_admin_grant(conn, U_ORGADMIN, ORG)
    check(granted is True, "the refused user is a REAL org admin (an org_admin user_roles grant)")
    count_q = "SELECT count(*) FROM portfolio.edgar_pipeline_runs"
    for method, path, body, ok in (("GET", f"{BASE}/workers", None, 200), ("POST", f"{BASE}/runs", QUEUE_BODY, 202)):
        n0 = await rls_val(conn, count_q)
        c1, b1 = await api(U_ORGADMIN, method, path, body)
        n1 = await rls_val(conn, count_q)
        c2, b2 = await api(U_SUPER, method, path, body)
        if method == "POST":
            await cancel_new(conn, q, b2)
        leaked = "laptop_start_command" in (b1 or {}) or "laptop_instructions" in (b1 or {})
        check(c1 == 403 and n0 == n1 and not leaked and c2 == ok,
              f"{method} {path}: org admin 403 (nothing written, no instructions leaked), super admin {c2}",
              f"org admin HTTP {c1}: {str(b1)[:60]}; super HTTP {c2}")


def y_frontend() -> None:
    section("[Y] Front end — node tests on the dialog logic and the REAL dialog's render")
    try:
        proc = subprocess.run(["node", "--test", NODE_TEST], cwd=str(WEB_DIR), capture_output=True, text=True,
                              timeout=300)
        out = proc.stdout + proc.stderr
        m_pass = re.search(r"^# pass (\d+)", out, re.M)
        m_fail = re.search(r"^# fail (\d+)", out, re.M)
        check(proc.returncode == 0 and m_pass is not None and int(m_pass.group(1)) > 0
              and m_fail is not None and int(m_fail.group(1)) == 0,
              f"node --test {NODE_TEST} passes", f"{m_pass.group(0) if m_pass else ''} {m_fail.group(0) if m_fail else out[-200:]}")
        oks = set(re.findall(r"^ok \d+ - (.+)$", out, re.M))
        for name, label in (
            ("the dialog opens after a Laptop queue (offline and online), carrying the run id",
             "the dialog opens after a Laptop queue"),
            ("status: amber when offline, green with the host when online", "amber when offline, green with host when online"),
            ("start steps collapse ONLY when the worker is online; the others stay visible",
             "start steps collapsed only when online"),
            ("every step with a command renders a monospace box and a Copy button; the command is the server's",
             "every step with a command renders a Copy button"),
            ("queueAndOpen POSTs exactly once; 'Show these steps' opens with no POST and no run",
             "\"Show these steps\" opens without a POST"),
            ("don't-show suppresses the dialog ONLY when the worker is online", "don't-show suppresses only when online"),
            ("no dialog for Render (refused or accepted), nor for a failed queue", "no dialog for Render"),
        ):
            check(name in oks, f"node test: {label}", "ok" if name in oks else "missing or failing")
    except Exception as exc:  # noqa: BLE001
        check(False, "node tests for the dialog ran", f"{type(exc).__name__}: {exc}")


def y_frontend_source() -> None:
    section("[Y] Front end source — steps rendered from laptop_instructions, no hard-coded command; build 0")
    dialog = DIALOG_JSX.read_text(encoding="utf-8") if DIALOG_JSX.exists() else ""
    bulk = BULK_JSX.read_text(encoding="utf-8")
    logic = BULK_LOGIC.read_text(encoding="utf-8")
    check("laptopDialogSteps(workers" in dialog and re.search(r"workers\?\.laptop_instructions", logic) is not None,
          "the dialog renders the steps from workers.laptop_instructions (via laptopDialogSteps)")
    check("laptopDialogStatus(workers" in dialog and "<LaptopWorkerDialog" in bulk and "workers={workers}" in bulk,
          "the dialog's status comes from the live GET workers payload")
    needles = ("doppler run", "run_bulk_fetch", "venv/bin/python", "tmux new -s", "tmux attach", "--worker",
               "/mnt/c/Users/Joe", "Settings → System → Power")
    for path, src in ((DIALOG_JSX, dialog), (BULK_JSX, bulk), (BULK_LOGIC, logic)):
        hits = [n for n in needles if n in src]
        check(bool(src) and not hits, f"{path.name} holds no hard-coded copy of the command or the step text",
              f"{hits}")
    m = re.search(r"function openLaptopSteps\(\) \{(.*?)\n  \}", bulk, re.S)
    body = m.group(1) if m else ""
    check(m is not None and "stepsDialog()" in body and "queue" not in body.lower().replace("queuebutton", ""),
          "\"Show these steps\" calls openLaptopSteps, which opens stepsDialog() and queues nothing",
          body.strip()[:120])
    check(re.search(r"onClick=\{openLaptopSteps\}\s*>\s*Show these steps", bulk) is not None,
          "the \"Show these steps\" control sits in the tab and is wired to openLaptopSteps")
    m = re.search(r"function closeLaptopDialog\(\) \{(.*?)\n  \}", bulk, re.S)
    check(m is not None and "opener?.focus()" in m.group(1)
          and "openerRef.current = queueButtonRef.current" in bulk and "ref={queueButtonRef}" in bulk,
          "closing returns focus to the opener — the Queue button after a queue")
    check('e.key === "Escape"' in dialog and "panel.current?.focus()" in dialog and 'aria-modal="true"' in dialog,
          "the dialog takes focus on open and closes on Esc")
    check("LAPTOP_DIALOG_RECHECK_MS" in bulk and re.search(r"LAPTOP_DIALOG_RECHECK_MS = 5000", logic) is not None
          and "setInterval(refreshWorkers, LAPTOP_DIALOG_RECHECK_MS)" in bulk,
          "worker status is re-checked every 5 s while the dialog is open")
    check(re.search(r"try \{\s*return window\.localStorage;", bulk) is not None
          and re.search(r"export function readHideWhenOnline[\s\S]*?try \{", logic) is not None,
          "the don't-show preference uses localStorage wrapped in try/catch")
    try:
        proc = subprocess.run(["npm", "run", "build"], cwd=str(WEB_DIR), capture_output=True, text=True, timeout=900)
        check(proc.returncode == 0, "npm run build exits 0", (proc.stdout + proc.stderr)[-300:] if proc.returncode else "")
        hits = [p for p in (WEB_DIR / ".next").rglob("*.js") if p.stat().st_size < 5_000_000
                and "Run on your laptop" in p.read_text(errors="ignore")] if proc.returncode == 0 else []
        check(bool(hits), "the built output contains the Run on your laptop dialog", f"{len(hits)} chunk(s)")
    except Exception as exc:  # noqa: BLE001
        check(False, "npm run build ran", f"{type(exc).__name__}: {exc}")


def y_no_model_no_network() -> None:
    section("[Y] No AI model calls; no SEC or R2 use; Render only read")
    check(MODEL_CALLS == [], "the mocked model clients recorded no call", f"{MODEL_CALLS}")
    check(R2 == {}, "nothing was written to (mocked) R2", f"{len(R2)} keys")
    methods = sorted({m for m, _ in RENDER_CALLS})
    check(set(methods) <= {"GET"}, "every Render API call (if any) was a GET", f"{len(RENDER_CALLS)} calls {methods}")


def y_status_doc() -> None:
    section("[Y] PROJECT_STATUS records the dialog, the setting name and its default")
    text = STATUS_DOC.read_text(encoding="utf-8")
    m = re.search(r"^## 0+\. BULKFETCH UI 2 — .*$", text, re.M)
    check(m is not None, "docs/PROJECT_STATUS.md has the BULKFETCH UI 2 entry", m.group(0)[:90] if m else "no match")
    for needle in ("Run on your laptop", SETTING, EXPECTED_DEFAULT, "Show these steps", "How to start the worker"):
        check(needle in text, f"PROJECT_STATUS covers {needle[:60]}")


# ═══ Regression ════════════════════════════════════════════════════════════
REGRESSION = "verify_bulkfetchui.py"


async def real_workers_live(conn, q) -> list:
    return [dict(r) for r in await rls_fetch(
        conn, f"""SELECT worker_id, target, host FROM portfolio.edgar_fetch_workers
                  WHERE stopped_at IS NULL AND worker_id NOT LIKE $1
                    AND heartbeat_at >= now() - interval '{q.WORKER_LOST_SECONDS} seconds'""", WORKER_PREFIX + "%")]


def y_regression() -> None:
    if os.environ.get("VERIFY_NESTED") == "1":
        print("[SKIP] nested run: regression section skipped (parent runs it)")
        return
    section(f"[Y] Regression: {REGRESSION} — ONCE, a flat subprocess, AFTER this script's own teardown")
    log_dir = REPO / "sprint_prompts" / "logs"
    try:
        proc = subprocess.run([sys.executable, str(HERE.parent / REGRESSION)], capture_output=True, text=True,
                              timeout=1200, cwd=str(REPO), env=os.environ | {"VERIFY_NESTED": "1"})
        out = proc.stdout + proc.stderr
    except subprocess.TimeoutExpired as exc:
        out = f"TIMEOUT after 1200s: {(exc.stdout or b'')[-300:]!r}"
    try:
        log_dir.mkdir(parents=True, exist_ok=True)
        (log_dir / f"verify_bulkfetchui2.regression.{REGRESSION.removesuffix('.py')}.out").write_text(out)
    except OSError:
        pass
    totals = re.findall(r"^TOTAL: (\d+) PASS, (\d+) FAIL", out, re.M)
    m = totals[-1] if totals else None
    fails = [ln for ln in out.splitlines() if ln.startswith("[FAIL]")]
    check(m is not None and int(m[0]) > 0 and int(m[1]) == 0, f"{REGRESSION} still reports 0 FAIL",
          (f"TOTAL: {m[0]} PASS, {m[1]} FAIL" if m else out[-300:]) + (f"; {fails[:3]}" if fails else ""))


# ═══ Main ══════════════════════════════════════════════════════════════════
async def preconditions(conn, q) -> bool:
    section("[Y] Preconditions — no real worker online (it could claim a fixture run, and fakes 'online')")
    live = await real_workers_live(conn, q)
    return check(not live, "no real worker is heartbeating (stop it: tmux attach -t bulkworker, Ctrl-C)", f"{live}")


async def main_async() -> int:
    url = await bootstrap_async()
    if not url:
        print("[FAIL] no working DATABASE_URL from Doppler")
        print("TOTAL: 0 PASS, 1 FAIL")
        return 1
    os.environ["DATABASE_URL"] = url
    doppler_setting = os.environ.get(SETTING)
    install_mocks()
    from services import edgar_bulk_queue as q
    conn = await asyncpg.connect(url, statement_cache_size=0)
    pre_counts = None
    gold_before = None
    clear = False

    async def run(name, fn, *a):
        try:
            res = fn(*a)
            if asyncio.iscoroutine(res):
                res = await res
            return res
        except Exception as exc:  # noqa: BLE001
            import traceback
            traceback.print_exc()
            check(False, f"section '{name}' ran to completion", f"{type(exc).__name__}: {exc}")
            return None

    try:
        if doppler_setting is not None:
            find(f"{SETTING} IS set in Doppler — the live API uses it, not the default",
                 f"{len(doppler_setting)} chars (value not printed); this run removes it in-process only")
        bypass = await conn.fetchval("SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user")
        check(bypass is False, "the verify connection's role does NOT bypass RLS",
              f"{await conn.fetchval('SELECT current_user')}")
        try:
            await teardown(conn)
        except Exception as exc:  # noqa: BLE001
            check(False, "start-of-run teardown", f"{type(exc).__name__}: {exc}")
        left0 = await fixture_counts(conn)
        check(sum(left0.values()) == 0, "no fixture rows before the run", json.dumps(left0))
        pre_counts = await table_counts(conn)
        gold_before = await gold_checksum(conn)
        run_defs = await check_defs(conn, "portfolio.edgar_pipeline_runs")
        check({"queued", "cancelled"} <= allowed(run_defs, "status") and "laptop" in allowed(run_defs, "target"),
              "the run values this script relies on (status queued/cancelled, target laptop) are live CHECK values")

        await run("task 1", y_task1)
        clear = bool(await run("preconditions", preconditions, conn, q))
        await run("seed", seed, conn)
        if clear:
            await run("setting", y_setting, conn, q)
            await run("instructions", y_instructions, q)
            await run("queue laptop", y_queue, conn, url, q)
            await run("render", y_render, conn, q)
            await run("permission gate", y_permissions, conn, q)
        else:
            check(False, "API sections skipped: a real worker is live (see preconditions)")
        y_no_model_no_network()
        await run("front end", y_frontend)
        await run("front end source", y_frontend_source)
        await run("status doc", y_status_doc)
    except Exception as exc:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        check(False, "verify ran to completion", f"{type(exc).__name__}: {exc}")
    finally:
        if doppler_setting is None:
            os.environ.pop(SETTING, None)
        else:
            os.environ[SETTING] = doppler_setting
        section("[Y] Teardown: zero fixture rows; every touched table at its pre-test count; real gold untouched")
        teardown_clean = False
        no_real_worker = False
        try:
            await teardown(conn)
            left = await fixture_counts(conn)
            teardown_clean = sum(left.values()) == 0
            check(teardown_clean, "zero fixture rows remain", json.dumps(left))
            if pre_counts is not None:
                post = await table_counts(conn)
                diff = {t: (pre_counts[t], post[t]) for t in COUNTED if pre_counts[t] != post[t]}
                check(not diff, "every touched table is back at its exact pre-test count", json.dumps(diff))
                teardown_clean = teardown_clean and not diff
            else:
                check(False, "pre-test counts were taken")
                teardown_clean = False
            gold_after = await gold_checksum(conn)
            check(gold_before is not None and gold_after == gold_before,
                  "existing note_gold_values rows unchanged (count and checksum)", f"{gold_before} -> {gold_after}")
            live = await real_workers_live(conn, q)
            no_real_worker = not live
            if live:
                check(False, "regression skipped: a real worker is heartbeating", f"{live}")
        except Exception as exc:  # noqa: BLE001
            check(False, "end-of-run teardown", f"{type(exc).__name__}: {exc}")
        try:
            from services.database import close_pool
            await close_pool()
        except Exception:  # noqa: BLE001
            pass
        await conn.close()
        if teardown_clean and no_real_worker:
            y_regression()
        elif not teardown_clean:
            check(False, "regression skipped: this run's own teardown did not leave zero fixture rows")
    print(f"\nTOTAL: {_n_pass} PASS, {_n_fail} FAIL")
    return 0 if _n_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main_async()))
