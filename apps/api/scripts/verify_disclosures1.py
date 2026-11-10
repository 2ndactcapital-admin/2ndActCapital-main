"""verify_disclosures1 — ONE disclosure library: keys, versions (each a FILE in R2 + its summary), the resolver
(platform → org → household → account, as-of), R2 folders (hashed, immutable, org-isolated reads), maker-checker
publish, acknowledgements by VERSION (re-acknowledge after a new one), the migration of today's text with an
exact-text fallback, the platform defaults, the admin page.

Pass/fail only, no prompts. Prints 'TOTAL: N PASS, M FAIL' on every exit path and exits non-zero on any failure.

* AUTHZ_TEST_MODE=1 is set before anything imports ``main``.
* Secrets are hydrated from Doppler over HTTPS (``_db_connect.hydrate``).
* NO NETWORK.
  * R2 goes through a fake storage transport at the CURRENT ``services.storage`` signatures:
    ``upload_bytes(key, data, content_type=None, bucket=None)`` and ``download_bytes(key, bucket=None)``. The
    signatures are read before the swap. ``get_s3_client`` is replaced by a recorder that raises, and "no real
    client was built" is asserted.
  * The Auth0 profile fetch is stubbed, and httpx's network transport refuses.

Everything after the bearer token is the real app against the live database:

* Two fixture orgs, 99000000-…-000000620001 and …0002, both ``is_test = true`` (asserted).
* Fixture users, ``is_test`` too. Their roles come through user_roles: ``grant_org_admin`` for org admins,
  ``grant_named_role('super_admin')`` for the two Super Admins, an explicit viewer role, and a member role.
  ``load_principal`` is asserted to decide each one as intended.
* Fixture keys use the reserved prefix ``dv1test.``. The one REAL key touched is ``long_short.risks``, as an ORG
  override in fixture org A only; the platform rows of real keys are never written.
* Reserved prefixes are asserted to match no real row.
* Constraints and unique indexes are read from the catalog before seeding.
* Teardown runs at the start and at the end, through ``_verify_cleanup`` by fixture id, with the
  ``execution.teardown`` GUC (the guards' fixture path).
* Every touched table is re-read for its exact pre-test count BY FIXTURE IDS. Real activity writes
  audit_log / users / accounts, and real users may acknowledge.
* Real disclosure rows are fingerprinted before and after.

--self-checks-only: git / route-record / static / pure checks (stdlib + git; no Doppler, DB, network or npm).
--cleanup-only: the teardown + leftover check only.
With VERIFY_NESTED=1 the regression section prints one '[SKIP] nested run'.
"""

from __future__ import annotations

import os as _authz_os
_authz_os.environ["AUTHZ_TEST_MODE"] = "1"  # AUTHZ1: set before main is imported

import ast
import asyncio
import base64
import hashlib
import inspect
import json
import os
import pathlib
import re
import subprocess
import sys
import traceback
import uuid
from datetime import date, datetime, timedelta, timezone
from uuid import UUID

HERE = pathlib.Path(__file__).resolve().parent
API_DIR = HERE.parent
REPO = API_DIR.parents[1]
WEB_DIR = REPO / "apps" / "web"
for _site in sorted(API_DIR.glob("venv/lib/python3*/site-packages")):   # the pure self-checks import services
    if str(_site) not in sys.path:
        sys.path.insert(0, str(_site))
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(API_DIR))

import _verify_cleanup as VC  # noqa: E402  (stdlib + asyncpg only)
import _verify_self as VS  # noqa: E402
import _verify_total as VT  # noqa: E402  (stdlib only)
import _verify_roles as VR  # noqa: E402  — every fixture role is a user_roles grant the app decides as meant

SPRINT_KEY = "disclosures1"
BEFORE_REF = "42c5c57"   # disclosures1-work base: "merge catchall1"
_D = "/api/v1/disclosures"

#: the sprint's own routes and the gate each carries (docs/AUTHZ1_ROUTES.json "after") — the sprint's spec
NEW_ROUTES = {
    ("GET", _D): "view_disclosures",
    ("GET", f"{_D}/resolve"): "view_disclosures",
    ("GET", f"{_D}/keys/{{key}}"): "view_disclosures",
    ("GET", f"{_D}/households/{{household_id}}"): "view_disclosures",
    ("GET", f"{_D}/accounts/{{account_id}}"): "view_disclosures",
    ("POST", f"{_D}/keys/{{key}}/versions"): "manage_disclosures",
    ("POST", f"{_D}/versions/{{version_id}}/publish"): "manage_disclosures",
    ("POST", f"{_D}/versions/{{version_id}}/retire"): "manage_disclosures",
    ("GET", f"{_D}/mine"): "none beyond auth",
    ("GET", f"{_D}/versions/{{version_id}}/file"): "none beyond auth",
    ("POST", f"{_D}/acknowledgements"): "none beyond auth",
}
#: the brief's library keys (Tasks 2 / 6 / 7) — a spec, not live state
BRIEF_KEYS = ("long_short.risks", "section351.exit", "direct_indexing.tax_overlay", "hypothetical.performance",
              "tax.estimate_label", "prices.internal_use_notice", "options.overlay", "notes.replica")
#: Task 1a's migrated disclosures → (BEFORE_REF file, the constant holding the exact old text)
MIGRATED = {
    "long_short.risks": ("apps/api/services/long_short_rules.py", "RISKS"),
    "hypothetical.performance": ("apps/api/services/hypothetical_policy.py", "NO_POLICY"),
    "tax.estimate_label": ("apps/api/services/tax_profiles.py", "ESTIMATE"),
    "prices.internal_use_notice": ("apps/api/services/security_master_jobs.py", "PRICE_GAP_NOTE"),
    "replica.fund_affiliation": ("apps/api/services/replica_terms.py", "disclosure_for"),
}
MARGIN_RISK_KEYS = ("short_unlimited_loss", "margin_calls", "borrow_recall", "borrow_cost_changes")
NODE_TEST_FILE = "tests/disclosures1/disclosures1.test.mjs"


def _read(rel: str) -> str:
    p = REPO / rel
    return p.read_text(encoding="utf-8") if p.exists() else ""


def run_cmd(args, cwd, timeout=600, env=None):
    """Foreground subprocess; never backgrounded, never interactive."""
    try:
        p = subprocess.run(args, cwd=str(cwd), capture_output=True, text=True, timeout=timeout,
                           env=env or os.environ.copy(), stdin=subprocess.DEVNULL)
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired as exc:
        return 124, str(exc.stdout or ""), f"timeout after {timeout}s"
    except FileNotFoundError as exc:
        return 127, "", str(exc)


def old_text(key: str) -> str:
    """The EXACT text the code carried at BEFORE_REF, read from that commit's source (never from today's code)."""
    rel, name = MIGRATED[key]
    src = VS.git_show(BEFORE_REF, rel) or ""
    tree = ast.parse(src)
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(isinstance(t, ast.Name) and t.id == name for t in targets):
                val = ast.literal_eval(node.value)
                if key == "long_short.risks":
                    return "\n".join(f"- [{k}] {t}" for k, t in val)
                return val
        if isinstance(node, ast.FunctionDef) and node.name == name:      # disclosure_for(ticker) at BEFORE_REF
            ns: dict = {}
            exec(compile(ast.Module(body=[node], type_ignores=[]), "<before>", "exec"), ns)   # noqa: S102
            return ns[name]("{ticker}").replace("{TICKER}", "{ticker}")
    raise LookupError(f"{name} not found in {rel} at {BEFORE_REF}")


# ═══ --self-checks-only (no Doppler / DB / network / npm) ═══════════════════════════════════════════════════════
TASK1 = [
    ("docs/DISCLOSURES1_DISCOVERY.md", "## 1a.", "1a — every place disclosure text is shown (table)"),
    ("docs/DISCLOSURES1_DISCOVERY.md", "long_short.risks", "1a — the rep13 risks → long_short.risks"),
    ("docs/DISCLOSURES1_DISCOVERY.md", "`/disclosures` has no page", "1a — the dead footer link is recorded"),
    ("docs/DISCLOSURES1_DISCOVERY.md", "## 1b.", "1b — storage.py signatures and the R2 key layouts"),
    ("docs/DISCLOSURES1_DISCOVERY.md", "proxied", "1b — the proxied read path chosen"),
    ("docs/DISCLOSURES1_DISCOVERY.md", "## 1c.", "1c — the existing acknowledgement records"),
]
CODE_NEEDLES = [
    ("apps/api/services/disclosures.py", "RESOLVE_SQL = f\"\"\"", "the resolver is ONE query (RESOLVE_SQL)"),
    ("apps/api/services/disclosures.py", "CASE v.scope WHEN 'account' THEN 4 WHEN 'household' THEN 3 WHEN 'org' THEN 2",
     "the most specific scope wins (account > household > org > platform)"),
    ("apps/api/services/disclosures.py", "await run_in_threadpool(storage.upload_bytes, r2_key, data, ctype",
     "uploads go through services.storage.upload_bytes (module attribute — the verify's fake replaces it)"),
    ("apps/api/services/disclosures.py", "storage.download_bytes, row[\"r2_key\"]",
     "reads go through services.storage.download_bytes, proxied"),
    ("apps/api/services/long_short.py", "lib = await library_risks(conn, org_id, account[\"id\"])",
     "the long/short build reads long_short.risks through the resolver"),
    ("apps/api/services/long_short.py", "why = await _library_refusal(conn, org_id, p)",
     "submit / approval refuse a stale acknowledgement (the version in force NOW)"),
    ("apps/api/services/hypothetical_policy.py", "async def gate_for(", "the hypothetical gate reads the library"),
    ("apps/api/routers/security_master.py", "DL.resolve_safe(conn, \"prices.internal_use_notice\"",
     "the prices internal-use notice is read through the resolver"),
    ("apps/api/routers/tax.py", "DL.resolve_safe(conn, \"tax.estimate_label\"", "the tax estimate label via the resolver"),
    ("apps/api/services/replica_terms.py", "\"replica.fund_affiliation\"", "the fund-replica text via the resolver"),
    ("docs/disclosures1_part1.sql", "disclosure_versions_maker_checker_chk CHECK (published_by IS NULL OR "
                                    "published_by <> drafted_by)", "maker-checker is also a CHECK"),
    ("docs/disclosures1_part1.sql", "REFERENCES public.disclosure_versions (id, content_sha256)",
     "an acknowledgement names the version id AND its hash (composite FK)"),
    ("apps/web/lib/menuVisibility.mjs", '"/admin/disclosures", "Disclosures", GATE_VIEW_DISCLOSURES',
     "Admin → Settings → Disclosures (nav)"),
]


def _key(rec: dict) -> tuple:
    return ((rec.get("methods") or [""])[0], rec["path"])


def self_task1(check, find):
    for rel, needle, meaning in TASK1:
        check(needle in _read(rel), f"Task 1 — {meaning}", f"{rel}: needle {needle!r} missing")
    find("1a — the prompt's 'estimate — confirm with your tax adviser' text existed nowhere; the only label was "
         "'estimate' (kept as the exact fallback; the sentence is the new default)")
    find("1a — the fee invoice disclosure is already a managed fee41 template (money-adjacent) — not moved")
    find("1c — rep13's acknowledgement recorded items and texts but no version or hash")


def self_routes(check, find):
    own = VS.own_routes(BEFORE_REF, SPRINT_KEY)
    new = {_key(own.end[k]): own.end[k] for k in own.new}
    check(set(new) == set(NEW_ROUTES), f"the sprint added exactly its {len(NEW_ROUTES)} routes",
          f"{own.where()}; extra {sorted(set(new) - set(NEW_ROUTES))}; missing {sorted(set(NEW_ROUTES) - set(new))}")
    wrong = [(k, (new.get(k) or {}).get("after")) for k, want in NEW_ROUTES.items()
             if want not in str((new.get(k) or {}).get("after") or "")]
    check(not wrong, "each new route carries its stated gate", f"{wrong}")
    check(not own.removed and not own.changed, "no existing route removed or its gate changed",
          f"removed {own.removed} changed {own.changed}")
    drift = VS.still_gated(own)
    check(not drift, "the sprint's routes still carry the same gate today", f"{drift}")


def self_static(check, find):
    for rel, needle, meaning in CODE_NEEDLES:
        check(needle in _read(rel), meaning, f"{rel}: needle missing")
    for k in BRIEF_KEYS:
        body = _read(f"docs/disclosures/defaults/{k}.md")
        check(bool(body and "default — have counsel review" in body and f"key: {k}" in body),
              f"Task 7 — docs/disclosures/defaults/{k}.md exists, marked 'default — have counsel review'")
    ls = _read("docs/disclosures/defaults/long_short.risks.md")
    check(all(f"[{k}]" in ls for k in MARGIN_RISK_KEYS), "long_short.risks default adds the margin / short-sale risks",
          f"{[k for k in MARGIN_RISK_KEYS if f'[{k}]' not in ls]}")
    s351 = _read("docs/disclosures/defaults/section351.exit.md")
    check(all(x in s351 for x in ("25 %", "50 %", "80 %", "looked through", "carryover basis", "prospectus")),
          "section351.exit covers the 25/50 test (looked through), the 80 % control test, basis, the prospectus")
    di = _read("docs/disclosures/defaults/direct_indexing.tax_overlay.md")
    check(all(x in di for x in ("$3,000", "Wash sales", "front-loaded", "Tracking error", "Not tax, legal or estate")),
          "direct_indexing.tax_overlay follows the structure (wash sale, $3,000 cap, front-loaded, not advice)")
    check("verify_disclosures1.py" in _read("scripts/regression_set.txt"), "Task 9: in scripts/regression_set.txt")
    doc = _read("docs/DISCLOSURES.md")
    check(all(x in doc for x in ("disclosures/platform/<key>/v<N>-<sha256>.<ext>", "most specific PUBLISHED",
                                 "--dry-run", "Every new disclosure goes through the library")),
          "Task 9: docs/DISCLOSURES.md states the layout, the resolver rule, the operator step and the platform rule")
    status = _read("docs/PROJECT_STATUS.md")
    m = re.search(r"\n## [^\n]*DISCLOSURES1[^\n]*\n.*?(?=\n## |\Z)", status, re.S)
    check(bool(m and "verify_disclosures1.py" in m.group(0)), "Task 9: PROJECT_STATUS has a DISCLOSURES1 section")


def self_pure(check, find):
    """Pure functions (imports local: --self-checks-only reaches here before hydrate)."""
    from services import disclosures as DL
    from services import long_short_rules as R

    for k in MIGRATED:
        try:
            want = old_text(k)
        except Exception as exc:  # noqa: BLE001
            check(False, f"the BEFORE_REF text of {k} is readable", f"{type(exc).__name__}: {exc}")
            continue
        check(DL.fallback_text(k) == want, f"migration: {k}'s fallback is EXACTLY its pre-library text (BEFORE_REF)",
              f"{DL.fallback_text(k)!r} vs {want!r}")
    items = DL.items_of(DL.fallback_text("long_short.risks"))
    check([(i["key"], i["text"]) for i in items] == list(R.RISKS),
          "the long/short fallback round-trips to rep13's six (key, text) pairs", f"{items}")
    k = DL.object_key(key="a.b", scope="household", org_id="O", household_id="H", version_no=3, sha256="f" * 64,
                      ext="pdf")
    check(k == f"disclosures/orgs/O/households/H/a.b/v3-{'f' * 64}.pdf", "the R2 key layout (household)", k)
    check(DL.object_key(key="a.b", scope="platform", version_no=1, sha256="0" * 64, ext="md")
          == f"disclosures/platform/a.b/v1-{'0' * 64}.md", "the R2 key layout (platform)")
    d = R.disclosures(borrow_total=R.ZERO, rebate_total=R.ZERO, margin={}, gross_long=R.ONE, gross_short=R.ZERO,
                      risks=[("x1", "X one")])
    d0 = R.disclosures(borrow_total=R.ZERO, rebate_total=R.ZERO, margin={}, gross_long=R.ONE, gross_short=R.ZERO)
    check(bool([r["key"] for r in d["risks"]] == ["x1"] and [r["key"] for r in d0["risks"]] == list(R.RISK_KEYS)),
          "the proposal carries the library's risks; with none, the built-in six")
    for bad in ("x.exe", "noext", "a.pdf.zip"):
        try:
            DL.file_kind(bad)
            ok = False
        except DL.DisclosureError:
            ok = True
        check(ok, f"file type refused: {bad}")
    check(DL.file_kind("A.HTM") == ("html", "text/html") and DL.file_kind("x.docx")[0] == "docx",
          "PDF / DOCX / MD / HTML accepted")


def self_checks(check, find):
    self_task1(check, find)
    self_routes(check, find)
    self_static(check, find)
    self_pure(check, find)


if __name__ == "__main__" and VS.requested():
    sys.exit(VS.run_self_checks(self_checks, SPRINT_KEY, __file__))

# VERIFYFIX1 — TOTAL on every exit path (a crash at import, an early sys.exit, a SIGTERM from `timeout` included).
import _verify_total as VT  # noqa: E402  (stdlib only)
if __name__ == "__main__":
    VT.install_guard(lambda: (globals().get("_n_pass", 0), globals().get("_n_fail", 0)))

from _db_connect import admin_dsn, connect, hydrate, resolve_dsn  # noqa: E402

hydrate()  # BEFORE main is imported (Settings is lru_cached)

import asyncpg  # noqa: E402
import httpx  # noqa: E402

import _authz1_common as C  # noqa: E402
from services import disclosures as DL  # noqa: E402
from services import hypothetical_policy as HP  # noqa: E402
from services import long_short as LS  # noqa: E402
from services import rbac  # noqa: E402
from services import replica_terms as T  # noqa: E402
from services import storage  # noqa: E402
from services import users as users_mod  # noqa: E402
from services.authz import load_principal  # noqa: E402
from services.custody.base import AccountNumber  # noqa: E402
from services.custody.registry import get_or_create_salt  # noqa: E402
from services.database import close_pool  # noqa: E402

OPERATOR_DSN_VAR = "DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP"
TAG = "disclosures1verify"
NAME = "DV1V "            # reserved fixture name prefix
KEY_PREFIX = "dv1test."   # reserved fixture disclosure-key prefix


def _id(n: int) -> UUID:
    return UUID(f"99000000-0000-0000-0000-00000062{n:04d}")


ORG_A, ORG_B = _id(1), _id(2)
FIXTURE_ORGS = [ORG_A, ORG_B]
U_SUPER1, U_SUPER2, U_A1, U_A2, U_VIEW, U_MEMBER, U_B = (_id(10 + i) for i in range(7))
VIEW_PERMS = frozenset({"view_disclosures"})
USERS = {   # uid: (org, users.role mirror, short name, how the role is granted)
    U_SUPER1: (ORG_A, "super_admin", "super1", "super_admin"),
    U_SUPER2: (ORG_A, "super_admin", "super2", "super_admin"),
    U_A1: (ORG_A, "org_admin", "admin1", "org_admin"),
    U_A2: (ORG_A, "org_admin", "admin2", "org_admin"),
    U_VIEW: (ORG_A, "member", "viewer", "dv1_viewer"),
    U_MEMBER: (ORG_A, "member", "member", "member"),
    U_B: (ORG_B, "org_admin", "adminb", "org_admin"),
}
ENT_A1, ENT_A2, ENT_B = _id(30), _id(31), _id(32)
HH_A1, HH_A2, HH_B = _id(40), _id(41), _id(42)
ACC_A1, ACC_A2, ACC_A3, ACC_B = _id(50), _id(51), _id(52), _id(53)
ACCOUNTS = {ACC_A1: (ORG_A, ENT_A1, HH_A1), ACC_A2: (ORG_A, ENT_A1, HH_A1), ACC_A3: (ORG_A, ENT_A2, HH_A2),
            ACC_B: (ORG_B, ENT_B, HH_B)}
PROPOSAL = _id(90)        # a long/short proposal id the acknowledgement names (subject only — no proposal row)
K_ALPHA, K_MEMBER, K_STAFF = KEY_PREFIX + "alpha", KEY_PREFIX + "member", KEY_PREFIX + "staff"
FIXTURE_KEYS = {K_ALPHA: "both", K_MEMBER: "member", K_STAFF: "staff"}
REAL_KEY = "long_short.risks"

_n_pass = 0
_n_fail = 0
S: dict = {"v": {}}
BLOBS: dict[str, bytes] = {}
PUT_LOG: list[str] = []
GET_LOG: list[str] = []
S3_BUILT: list = []
NETWORK_CALLS: list = []
STORAGE = {"down": False}
CLAIMS_BY_TOKEN: dict[str, dict] = {}


def check(passed: bool, label: str, detail: str = "") -> bool:
    assert isinstance(passed, bool), f"check() got {passed!r} ({type(passed).__name__}) for {label!r}"
    global _n_pass, _n_fail
    print(f"{'[PASS]' if passed else '[FAIL]'} {label}" + (f"  — {detail}" if detail and not passed else ""))
    if passed:
        _n_pass += 1
    else:
        _n_fail += 1
    return passed


def find(label: str, detail: str = "") -> None:
    print(f"[FIND] {label}" + (f"  — {detail}" if detail else ""))


def section(title: str) -> None:
    print(f"\n── {title} ──")


def today() -> date:
    return date.today()


def sub_of(uid: UUID) -> str:
    return f"auth0|{TAG}-{USERS[uid][2]}"


def dumps(x) -> str:
    return json.dumps(x, ensure_ascii=False, default=str)


def content(label: str) -> bytes:
    """Each fixture upload has DISTINCT content (a fresh uuid), so every hash differs."""
    return f"# {NAME}{label}\n\nfixture disclosure {uuid.uuid4()}\n".encode()


# ═══ DB helpers (RLS context set ON the connection inside its transaction) ════════════════════════════════════
async def platform(conn, sql, *args, fetch="fetch"):
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        return await getattr(conn, fetch)(sql, *args)


async def scoped(conn, org, sql, *args, fetch="fetch"):
    async with conn.transaction():
        await conn.execute(
            "SELECT set_config('app.current_org_id', $1, true), set_config('app.is_super_admin', 'false', true),"
            " set_config('app.current_auth0_sub', '', true)", str(org) if org else "")
        return await getattr(conn, fetch)(sql, *args)


async def in_org(conn, org, fn):
    """Run ``fn(conn)`` (a service call) inside a transaction whose org GUC is set ON the connection."""
    async with conn.transaction():
        await conn.execute(
            "SELECT set_config('app.current_org_id', $1, true), set_config('app.is_super_admin', 'false', true),"
            " set_config('app.current_auth0_sub', '', true)", str(org))
        return await fn(conn)


async def operator_conn():
    dsn, how = await resolve_dsn(OPERATOR_DSN_VAR)
    if not dsn:
        raise RuntimeError(f"no operator DSN for the fixture teardown path: {how}")
    return await connect(dsn)


FIX_VERSIONS = "(org_id = ANY($1::uuid[]) OR drafted_by = ANY($2::uuid[]))"
FIX_CATALOG = "(key LIKE 'dv1test.%' OR created_by = ANY($2::uuid[]))"
#: every touched table, counted BY FIXTURE IDS (real activity writes users / accounts / audit_log, and real users
#: may acknowledge while this runs) — $1 fixture orgs, $2 fixture users
COUNTED = {
    "public.organizations": "id = ANY($1::uuid[])",
    "public.users": "id = ANY($2::uuid[])",
    "public.user_roles": "user_id = ANY($2::uuid[])",
    "public.entities": "org_id = ANY($1::uuid[])",
    "public.households": "org_id = ANY($1::uuid[])",
    "public.household_memberships": "household_id IN (SELECT id FROM public.households WHERE org_id = ANY($1::uuid[]))",
    "public.accounts": "org_id = ANY($1::uuid[])",
    "public.delegate_grants": "org_id = ANY($1::uuid[])",
    "public.disclosures": FIX_CATALOG,
    "public.disclosure_versions": FIX_VERSIONS,
    "public.disclosure_acknowledgements": "(org_id = ANY($1::uuid[]) OR user_id = ANY($2::uuid[]))",
    "public.audit_log": "(org_id = ANY($1::uuid[]) OR user_id = ANY($2::uuid[]))",
}


_PARAM_RX = re.compile(r"\$(\d+)\b")


def bind(sql: str, *args) -> tuple[str, list]:
    """Pass ONLY the arguments ``sql`` references, renumbered $1.. in order. The shared WHERE fragments name
    "$1 fixture orgs, $2 fixture users" but most reference one of them — asyncpg refuses a surplus argument
    ("the server expects 1 argument for this query, 2 were passed") and Postgres can't type an unreferenced $1
    ("could not determine data type of parameter $1"); that crashed the pre-run counts (disclosures1 fix 1)."""
    used = sorted({int(m) for m in _PARAM_RX.findall(sql)})
    if not used or used[-1] > len(args):
        raise ValueError(f"bind: {sql!r} references {used} but {len(args)} argument(s) were given")
    renum = {old: new for new, old in enumerate(used, 1)}
    return _PARAM_RX.sub(lambda m: f"${renum[int(m.group(1))]}", sql), [args[i - 1] for i in used]


async def counts(conn) -> dict:
    out = {}
    for t, where in COUNTED.items():
        sql, args = bind(f"SELECT count(*) FROM {t} WHERE {where}", FIXTURE_ORGS, list(USERS))
        out[t] = int(await platform(conn, sql, *args, fetch="fetchval"))
    return out


async def real_prints(conn) -> dict:
    """Real (non-fixture) library rows: count + checksum by id — they must be unchanged by this run."""
    out = {}
    for t, fx in (("public.disclosures", FIX_CATALOG), ("public.disclosure_versions", FIX_VERSIONS)):
        sql, args = bind(f"SELECT count(*) AS n, md5(COALESCE(string_agg(to_jsonb(t)::text, '|' ORDER BY t.id), "
                         f"'')) AS h FROM {t} t WHERE NOT {fx}", FIXTURE_ORGS, list(USERS))
        r = await platform(conn, sql, *args, fetch="fetchrow")
        out[t] = (int(r["n"]), r["h"])
    r = await platform(conn, "SELECT count(*) AS n, md5(COALESCE(string_agg(to_jsonb(t)::text, '|' ORDER BY t.id), "
                             "'')) AS h FROM public.disclosure_acknowledgements t WHERE NOT (org_id = ANY($1::uuid[]) "
                             "OR user_id = ANY($2::uuid[])) AND acknowledged_at < $3", FIXTURE_ORGS, list(USERS),
                       S["run_start"], fetch="fetchrow")
    out["public.disclosure_acknowledgements (before the run)"] = (int(r["n"]), r["h"])
    return out


async def _shared_catalog(op) -> list[UUID]:
    """Catalog rows a fixture user created (ensure_catalog on first use of a real key) that a NON-fixture version
    now references — kept (their created_by is cleared so the user can go), never deleted."""
    rows = await op.fetch(
        "SELECT d.id FROM public.disclosures d WHERE d.created_by = ANY($2::uuid[]) AND EXISTS (SELECT 1 FROM "
        "public.disclosure_versions v WHERE v.disclosure_id = d.id AND NOT (v.org_id = ANY($1::uuid[]) OR "
        "v.drafted_by = ANY($2::uuid[])))", FIXTURE_ORGS, list(USERS))
    return [r["id"] for r in rows]


async def teardown() -> list[str]:
    op = await operator_conn()
    try:
        users = list(USERS)
        shared = await _shared_catalog(op)
        if shared:
            async with op.transaction():
                await op.execute("UPDATE public.disclosures SET created_by = NULL WHERE id = ANY($1::uuid[])", shared)
            print(f"[INFO] kept {len(shared)} real-key catalog row(s) a real version now uses (created_by cleared)")
        cat = [r["id"] for r in await op.fetch(
            "SELECT id FROM public.disclosures WHERE key LIKE 'dv1test.%' OR created_by = ANY($1::uuid[])", users)]
        plan = {
            "public.audit_log": ("org_id = ANY($1::uuid[]) OR user_id = ANY($2::uuid[])", [FIXTURE_ORGS, users]),
            "public.user_roles": ("user_id = ANY($1::uuid[])", [users]),
            "public.role_permissions": ("role_id IN (SELECT id FROM public.roles WHERE org_id = ANY($1::uuid[]))",
                                        [FIXTURE_ORGS]),
            "public.household_memberships": ("household_id = ANY($1::uuid[])", [[HH_A1, HH_A2, HH_B]]),
            "public.disclosure_acknowledgements": ("org_id = ANY($1::uuid[]) OR user_id = ANY($2::uuid[])",
                                                   [FIXTURE_ORGS, users]),
            "public.disclosure_versions": ("org_id = ANY($1::uuid[]) OR drafted_by = ANY($2::uuid[]) OR "
                                           "disclosure_id = ANY($3::uuid[])", [FIXTURE_ORGS, users, cat]),
            "public.disclosures": ("id = ANY($1::uuid[])", [cat]),
        }
        res = await VC.cleanup(op, orgs=FIXTURE_ORGS, users=users, user_sub_like=f"%{TAG}%", plan=plan,
                               gucs=("execution.teardown",), label="verify_disclosures1")
        return res["log"]
    finally:
        await op.close()


async def leftovers(conn) -> int:
    op = await operator_conn()
    try:
        return await VC.leftover_count(op, orgs=FIXTURE_ORGS, users=list(USERS), user_sub_like=f"%{TAG}%", extra=[
            ("SELECT count(*) FROM public.disclosures WHERE key LIKE 'dv1test.%' OR created_by = ANY($1::uuid[])",
             [list(USERS)]),
            ("SELECT count(*) FROM public.disclosure_versions WHERE drafted_by = ANY($1::uuid[]) OR "
             "published_by = ANY($1::uuid[]) OR r2_key LIKE ANY($2::text[])",
             [list(USERS), [f"disclosures/orgs/{o}/%" for o in FIXTURE_ORGS] + ["%/dv1test.%"]]),
            ("SELECT count(*) FROM public.disclosure_acknowledgements WHERE user_id = ANY($1::uuid[])", [list(USERS)]),
            ("SELECT count(*) FROM public.audit_log WHERE org_id = ANY($1::uuid[]) OR user_id = ANY($2::uuid[])",
             [FIXTURE_ORGS, list(USERS)])])
    finally:
        await op.close()


async def cleanup_only(conn) -> int:
    import time as _time

    t0 = _time.monotonic()
    log: list[str] = []
    try:
        log = await teardown()
    except Exception as exc:  # noqa: BLE001
        check(False, "cleanup raised", f"{type(exc).__name__}: {exc}")
        traceback.print_exc()
    print(f"CLEANUP_SECONDS: {_time.monotonic() - t0:.1f}")
    left = await leftovers(conn)
    print(f"LEFTOVERS: {left}")
    print(f"[INFO] cleanup removed: {log}")
    await conn.close()
    check(left == 0, "--cleanup-only: zero fixture rows remain", f"{left}")
    print(f"\nTOTAL: {_n_pass} PASS, {_n_fail} FAIL")
    return 0 if _n_fail == 0 else 1


# ═══ Catalog + seed ═════════════════════════════════════════════════════════════════════════════════════════════
CATALOG_TABLES = ("public.disclosures", "public.disclosure_versions", "public.disclosure_acknowledgements",
                  "public.accounts", "public.households", "public.user_roles", "public.delegate_grants")


async def y_role_is_real(conn):
    section("[Y] The verify's DB role does not bypass RLS")
    r = await conn.fetchrow("SELECT current_user AS u, rolbypassrls FROM pg_roles WHERE rolname = current_user")
    check(bool(r["rolbypassrls"] is False), "the verify connection does not bypass RLS (rolbypassrls = false)",
          f"{r['u']}")


async def y_catalog(conn):
    section("[Y] Constraints + unique indexes read from the catalog before seeding; reserved prefixes match no real row")
    cons = {}
    for t in CATALOG_TABLES:
        for r in await platform(conn, "SELECT conname, contype::text AS k, pg_get_constraintdef(oid) AS d FROM "
                                      "pg_constraint WHERE conrelid = $1::regclass AND contype::text IN ('p','c','u',"
                                      "'f')", t):
            cons[(t, r["conname"])] = (r["k"], r["d"])
    kinds = {k for k, _ in cons.values()}
    check(bool(cons and {"p", "c", "f", "u"} <= kinds), f"{len(cons)} PK / CHECK / UNIQUE / FK constraints read",
          f"{sorted(kinds)}")
    S["cons"] = cons
    status = next((d for (t, n), (k, d) in cons.items() if n == "disclosure_versions_status_chk"), "")
    check(set(re.findall(r"'([a-z]+)'::text", status)) == set(DL.STATUS_LABELS),
          "the status CHECK allows exactly the service's statuses (draft / published / retired)", status)
    types = next((d for (t, n), (k, d) in cons.items() if n == "disclosure_versions_type_chk"), "")
    check(set(re.findall(r"'([^']+)'::text", types)) == set(DL.FILE_TYPES.values()),
          "the content-type CHECK allows exactly the service's file types", types)
    scope = next((d for (t, n), (k, d) in cons.items() if n == "disclosure_versions_scope_chk"), "")
    check(all(f"'{s}'" in scope for s in DL.SCOPES), "the scope CHECK names the four scopes", scope[:200])
    fk = next((d for (t, n), (k, d) in cons.items() if n == "disclosure_ack_version_fk"), "")
    check("(version_id, content_sha256)" in fk, "acknowledgement → (version id, hash) composite FK", fk)
    idx = {r["indexname"]: r["indexdef"] for r in await platform(
        conn, "SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = 'public' AND tablename = ANY($1)",
        ["disclosures", "disclosure_versions", "disclosure_acknowledgements"])}
    check(bool("UNIQUE" in idx.get("disclosures_key_live_uq", "") and "UNIQUE" in idx.get("disclosure_versions_no_uq", "")
               and "UNIQUE" in idx.get("disclosure_versions_r2_key_uq", "")),
          "unique indexes read: one live row per key; one live version number per (key, scope target); one live row "
          "per R2 key", f"{sorted(idx)}")
    for t in ("disclosures", "disclosure_versions", "disclosure_acknowledgements"):
        pol = {r["cmd"] for r in await platform(conn, "SELECT cmd FROM pg_policies WHERE schemaname = 'public' AND "
                                                      "tablename = $1", t)}
        rls = await platform(conn, "SELECT relrowsecurity FROM pg_class WHERE oid = $1::regclass", f"public.{t}",
                             fetch="fetchval")
        check(bool(pol == {"SELECT", "INSERT", "UPDATE", "DELETE"} and rls is True),
              f"{t}: RLS on, a policy for every operation", f"{sorted(pol)} rls {rls}")
    real = await platform(conn, "SELECT (SELECT count(*) FROM public.disclosures WHERE key LIKE 'dv1test.%') + "
                                "(SELECT count(*) FROM public.disclosure_versions WHERE r2_key LIKE '%/dv1test.%') + "
                                "(SELECT count(*) FROM entities WHERE display_name LIKE $1 AND NOT (org_id = "
                                "ANY($4::uuid[]))) + (SELECT count(*) FROM households WHERE name LIKE $1 AND NOT (org_id "
                                "= ANY($4::uuid[]))) + (SELECT count(*) FROM users WHERE auth0_sub LIKE $2 AND NOT (id = "
                                "ANY($5::uuid[]))) + (SELECT count(*) FROM organizations WHERE slug LIKE $3 AND NOT (id "
                                "= ANY($4::uuid[])))", NAME + "%", f"%{TAG}%", f"{TAG}%", FIXTURE_ORGS, list(USERS),
                          fetch="fetchval")
    check(real == 0, "the reserved fixture prefixes (dv1test. keys and R2 keys, 'DV1V ' names, subs, slugs) match no "
          "real row", f"{real}")


async def seed(conn) -> None:
    S["seed_day"] = today()
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        for org, nm in ((ORG_A, "a"), (ORG_B, "b")):
            await conn.execute("INSERT INTO organizations (id, name, slug, is_test) VALUES ($1, $2, $3, true)",
                               org, f"DISCLOSURES1 Verify Org {nm.upper()}", f"{TAG}-{nm}")
        perm_ids = {r["name"]: r["id"] for r in await conn.fetch("SELECT id, name FROM permissions")}
        missing = sorted((VIEW_PERMS | set(C.MEMBER_PERMS)) - set(perm_ids))
        if missing:
            raise RuntimeError(f"permission catalog lacks {missing}")
        rid_view = await rbac.ensure_role(conn, ORG_A, "dv1_viewer", "DISCLOSURES1 verify — view_disclosures only")
        for p in VIEW_PERMS:
            await conn.execute("INSERT INTO role_permissions (role_id, permission_id) VALUES ($1, $2) ON CONFLICT DO "
                               "NOTHING", rid_view, perm_ids[p])
        rid_member = await rbac.ensure_role(conn, ORG_A, "member", "DISCLOSURES1 verify — member")
        for p in C.MEMBER_PERMS:
            await conn.execute("INSERT INTO role_permissions (role_id, permission_id) VALUES ($1, $2) ON CONFLICT DO "
                               "NOTHING", rid_member, perm_ids[p])
        for uid, (org, account_role, nm, how) in USERS.items():
            await conn.execute(
                "INSERT INTO users (id, org_id, email, full_name, auth0_sub, role, status, is_active, is_test) "
                "VALUES ($1, $2, $3, $4, $5, $6, 'active', true, true)",
                uid, org, f"{TAG}-{nm}@test.local", f"DV1 {nm.title()}", sub_of(uid), account_role)
            if how == "org_admin":
                await rbac.grant_org_admin(conn, uid, org)
            else:
                await rbac.grant_named_role(conn, uid, org, how)
        for ent, org, nm in ((ENT_A1, ORG_A, "Member Family"), (ENT_A2, ORG_A, "Other Family"), (ENT_B, ORG_B, "B")):
            await conn.execute("INSERT INTO entities (id, org_id, entity_type, display_name, status) VALUES ($1, $2, "
                               "'individual', $3, 'active')", ent, org, NAME + nm)
        for hh, org, ent in ((HH_A1, ORG_A, ENT_A1), (HH_A2, ORG_A, ENT_A2), (HH_B, ORG_B, ENT_B)):
            await conn.execute("INSERT INTO households (id, org_id, name, created_by) VALUES ($1, $2, $3, $4)", hh, org,
                               f"{NAME}Household {str(hh)[-2:]}", U_A1 if org == ORG_A else U_B)
            await conn.execute("INSERT INTO household_memberships (household_id, entity_id, added_by) VALUES ($1, $2, "
                               "$3)", hh, ent, U_A1 if org == ORG_A else U_B)
        salts = {}
        for org in FIXTURE_ORGS:
            await conn.execute("SELECT set_config('app.current_org_id', $1, true)", str(org))
            salts[org] = await get_or_create_salt(conn, org)
        for i, (acc, (org, ent, hh)) in enumerate(ACCOUNTS.items()):
            n = AccountNumber(f"DV1{6200 + i:06d}")
            await conn.execute(
                "INSERT INTO accounts (id, org_id, account_number_masked, account_number_hash, custodian_code, "
                "registration_type, tax_status, primary_entity_id, household_id) VALUES ($1, $2, $3, $4, 'manual', "
                "'individual', 'taxable', $5, $6)", acc, org, n.masked, n.hashed(salts[org]), ent, hh)
        await conn.execute("INSERT INTO delegate_grants (org_id, principal_entity_id, delegate_user_id, scope) "
                           "VALUES ($1, $2, $3, 'view_only')", ORG_A, ENT_A1, U_MEMBER)
        for k, aud in FIXTURE_KEYS.items():
            await conn.execute("INSERT INTO public.disclosures (key, title, category, audience, description) VALUES "
                               "($1, $2, 'other', $3, 'DISCLOSURES1 verify fixture key')", k, f"{NAME}{k}", aud)
    S["seeded"] = True
    # VERIFYFIX1 (bugfix4: public.user_roles is the ONE role source; users.role only mirrors it) — assert the APP
    # decides every fixture user as the seed means (grants exactly, Super Admin / staff / permissions).
    await VR.check_roles_as_meant(conn, check, {uid: (org, {how}) for uid, (org, _a, _n, how) in USERS.items()})


async def y_fixtures(conn):
    section("[Y] Fixture orgs + users are is_test; roles come from user_roles and the app decides each as intended")
    o = await platform(conn, "SELECT count(*) FROM organizations WHERE id = ANY($1::uuid[]) AND is_test", FIXTURE_ORGS,
                       fetch="fetchval")
    u = await platform(conn, "SELECT count(*) FROM users WHERE id = ANY($1::uuid[]) AND is_test", list(USERS),
                       fetch="fetchval")
    check(bool(o == len(FIXTURE_ORGS) and u == len(USERS)), "every fixture org and user is is_test = true", f"{o}/{u}")
    pr = {}
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        for uid in USERS:
            pr[uid] = await load_principal(conn, uid)
    want = {U_SUPER1: (True, True, True), U_SUPER2: (True, True, True), U_A1: (False, True, True),
            U_A2: (False, True, True), U_VIEW: (False, True, False), U_MEMBER: (False, False, False),
            U_B: (False, True, True)}
    got = {uid: (bool(p.is_super_admin), p.has(DL.VIEW), p.has(DL.MANAGE)) for uid, p in pr.items()}
    check(got == want, "load_principal decides each fixture (super admin / view_disclosures / manage_disclosures) — a "
          "NEW org's org_admin holds both disclosure permissions by default", f"{got}")


# ═══ The real app, token stubbed by bearer string ═══════════════════════════════════════════════════════════════
def token_for(uid: UUID) -> str:
    key = f"t-{TAG}-{USERS[uid][2]}"
    CLAIMS_BY_TOKEN[key] = {"sub": sub_of(uid), "org_id": str(USERS[uid][0])}
    return key


async def api_raw(who: UUID, method, path, body=None, timeout=180.0):
    import main

    kw = {"headers": {"Authorization": f"Bearer {token_for(who)}"}}
    if body is not None:
        kw["json"] = body
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app, raise_app_exceptions=False),
                                 base_url="http://verify", timeout=timeout) as client:
        try:
            res = await asyncio.wait_for(client.request(method, path, **kw), timeout)
        except asyncio.TimeoutError:
            return 599, b"", {}
    return res.status_code, res.content, dict(res.headers)


async def api(who, method, path, body=None):
    code, raw, _h = await api_raw(who, method, path, body)
    try:
        return code, (json.loads(raw.decode("utf-8")) if raw else {})
    except ValueError:
        return code, {"raw": raw[:300].decode("utf-8", "replace")}


def install_stubs():
    """Each stub matches the CURRENT signature of what it replaces (read before the swap)."""
    import main

    have = list(inspect.signature(main.verify_token).parameters)
    if len(have) != 1:
        raise RuntimeError(f"verify_token signature drifted: {have}")
    main.verify_token = lambda tok: dict(CLAIMS_BY_TOKEN[tok])
    have = list(inspect.signature(users_mod.fetch_auth0_profile).parameters)
    if len(have) != 2:
        raise RuntimeError(f"fetch_auth0_profile signature drifted: {have}")

    async def no_profile(request, c):
        return None

    users_mod.fetch_auth0_profile = no_profile
    up = list(inspect.signature(storage.upload_bytes).parameters)
    down = list(inspect.signature(storage.download_bytes).parameters)
    if up != ["key", "data", "content_type", "bucket"] or down != ["key", "bucket"]:
        raise RuntimeError(f"storage signatures drifted: upload_bytes{up} download_bytes{down}")

    def fake_upload(key, data, content_type=None, bucket=None):
        if STORAGE["down"]:
            raise ConnectionError("fake R2: unavailable")
        if key in BLOBS:
            raise AssertionError(f"fake R2: an existing object was about to be overwritten: {key}")
        BLOBS[key] = bytes(data)
        PUT_LOG.append(key)
        return key

    def fake_download(key, bucket=None):
        if STORAGE["down"]:
            raise ConnectionError("fake R2: unavailable")
        GET_LOG.append(key)
        return BLOBS[key]

    def no_client(*a, **k):
        S3_BUILT.append("get_s3_client")
        raise AssertionError("verify: a real R2 client must never be built")

    storage.upload_bytes = fake_upload
    storage.download_bytes = fake_download
    storage.get_s3_client = no_client

    async def refuse_network(self, request):
        NETWORK_CALLS.append(str(request.url))
        raise AssertionError(f"verify_disclosures1: a network call was attempted: {request.url}")

    httpx.AsyncHTTPTransport.handle_async_request = refuse_network


async def upload(who, key, scope, summary, *, eff=None, hh=None, acct=None, data=None, name="d.md"):
    data = data if data is not None else content(f"{key} {scope} {summary[:20]}")
    body = {"scope": scope, "file_name": name, "content_base64": base64.b64encode(data).decode(), "summary": summary}
    if eff:
        body["effective_from"] = eff.isoformat()
    if hh:
        body["household_id"] = str(hh)
    if acct:
        body["account_id"] = str(acct)
    c, b = await api(who, "POST", f"{_D}/keys/{key}/versions", body)
    return c, b, data


async def publish(who, vid):
    return await api(who, "POST", f"{_D}/versions/{vid}/publish", {})


async def version_row(conn, vid) -> dict | None:
    r = await platform(conn, "SELECT * FROM public.disclosure_versions WHERE id = $1 AND system_to IS NULL", vid,
                       fetch="fetchrow")
    return dict(r) if r else None


async def vcount(conn) -> int:
    return int(await platform(conn, f"SELECT count(*) FROM public.disclosure_versions WHERE {FIX_VERSIONS}",
                              FIXTURE_ORGS, list(USERS), fetch="fetchval"))


# ═══ Sections ═══════════════════════════════════════════════════════════════════════════════════════════════════
async def y_r2(conn):
    section("[Y] R2 — upload writes the hashed key under the right folder; a second upload is a NEW object; refusals "
            "write nothing")
    c, b, data = await upload(U_A1, K_ALPHA, "org", "DV1 org v1", eff=S["seed_day"] - timedelta(days=30))
    v = b.get("version") or {}
    sha = hashlib.sha256(data).hexdigest()
    want = f"disclosures/orgs/{ORG_A}/{K_ALPHA}/v1-{sha}.md"
    row = await version_row(conn, v.get("id")) if v.get("id") else None
    check(bool(c == 201 and v.get("status") == "draft" and row and row["r2_key"] == want and PUT_LOG[-1:] == [want]
               and BLOBS.get(want) == data and row["content_sha256"] == sha and row["size_bytes"] == len(data)
               and row["drafted_by"] == U_A1),
          "an org upload is a DRAFT; the fake R2 recorded exactly disclosures/orgs/<org>/<key>/v1-<sha256>.md; the row "
          "holds that key, the hash and the size", f"{c} {dumps(b)} put {PUT_LOG[-2:]}")
    S["v"]["O1"] = v.get("id")
    c2, b2, data2 = await upload(U_A1, K_ALPHA, "org", "DV1 org v2 (later)", eff=S["seed_day"] - timedelta(days=20))
    v2 = b2.get("version") or {}
    sha2 = hashlib.sha256(data2).hexdigest()
    want2 = f"disclosures/orgs/{ORG_A}/{K_ALPHA}/v2-{sha2}.md"
    check(bool(c2 == 201 and v2.get("r2_key") == want2 and want2 in BLOBS and BLOBS.get(want) == data
               and want != want2 and sha != sha2),
          "a second upload is a NEW object (v2, its own hash); the first object is untouched (never overwritten)",
          f"{c2} {dumps(b2)}")
    S["v"]["O2"] = v2.get("id")
    c3, b3, d3 = await upload(U_SUPER1, K_ALPHA, "platform", "DV1 platform v1", eff=S["seed_day"] - timedelta(days=40))
    v3 = b3.get("version") or {}
    check(bool(c3 == 201 and v3.get("r2_key") == f"disclosures/platform/{K_ALPHA}/v1-{hashlib.sha256(d3).hexdigest()}.md"
               and v3.get("org_id") is None),
          "a Super Admin's platform default lands under disclosures/platform/<key>/", f"{c3} {dumps(b3)}")
    S["v"]["P1"] = v3.get("id")
    c4, b4, d4 = await upload(U_A1, K_ALPHA, "household", "DV1 household HH_A1", eff=S["seed_day"] - timedelta(days=40),
                              hh=HH_A1, name="hh.pdf")
    v4 = b4.get("version") or {}
    check(bool(c4 == 201 and v4.get("r2_key") == f"disclosures/orgs/{ORG_A}/households/{HH_A1}/{K_ALPHA}/v1-"
               f"{hashlib.sha256(d4).hexdigest()}.pdf" and v4.get("content_type") == "application/pdf"),
          "a household override lands under disclosures/orgs/<org>/households/<id>/<key>/ (PDF)", f"{c4} {dumps(b4)}")
    S["v"]["H1"] = v4.get("id")
    c5, b5, d5 = await upload(U_A1, K_ALPHA, "account", "DV1 account ACC_A1", eff=S["seed_day"] - timedelta(days=40),
                              acct=ACC_A1, name="acct.html")
    v5 = b5.get("version") or {}
    check(bool(c5 == 201 and v5.get("r2_key") == f"disclosures/orgs/{ORG_A}/accounts/{ACC_A1}/{K_ALPHA}/v1-"
               f"{hashlib.sha256(d5).hexdigest()}.html"),
          "an account override lands under disclosures/orgs/<org>/accounts/<id>/<key>/ (HTML)", f"{c5} {dumps(b5)}")
    S["v"]["A1"] = v5.get("id")
    c6, b6, _ = await upload(U_A1, K_ALPHA, "account", "DV1 draft only ACC_A2", eff=S["seed_day"] - timedelta(days=40),
                             acct=ACC_A2)
    S["v"]["A2_DRAFT"] = (b6.get("version") or {}).get("id")
    check(c6 == 201, "a draft-only account override (never published) on ACC_A2", f"{c6} {dumps(b6)}")
    # refusals: nothing written (count before == after), nothing stored
    n0, p0 = await vcount(conn), len(PUT_LOG)
    bad = [await upload(U_A1, K_ALPHA, "org", "bad type", name="x.exe"),
           await upload(U_A1, K_ALPHA, "org", "too big", data=b"x" * (DL.MAX_BYTES + 1), name="big.md"),
           await upload(U_A1, K_ALPHA, "household", "org B household", hh=HH_B)]
    STORAGE["down"] = True
    try:
        down = await upload(U_A1, K_ALPHA, "org", "storage down")
    finally:
        STORAGE["down"] = False
    n1, p1 = await vcount(conn), len(PUT_LOG)
    check(bool([x[0] for x in bad] == [422, 422, 404] and down[0] == 503 and n1 == n0 and p1 == p0),
          "refused, nothing recorded: a .exe (422), a file over 5 MB (422), another org's household (404), R2 "
          "unavailable (503 — the row rolls back with the failed store)",
          f"{[(x[0], dumps(x[1])[:120]) for x in bad]} {down[0]} {dumps(down[1])[:160]} rows {n0}->{n1}")
    n_store = await platform(conn, "SELECT count(*) FROM audit_log WHERE org_id = ANY($1::uuid[]) AND action = "
                                   "'disclosure.version_drafted' AND payload->>'key' = $2", FIXTURE_ORGS, K_ALPHA,
                       fetch="fetchval")
    check(n_store == 6, "each stored draft is audited once (6 drafts; the refused ones wrote no audit row)", f"{n_store}")


async def y_gates(conn):
    section("[Y] Gates on IDENTICAL valid requests — refused for the wrong caller, admitted for the right one")
    body = {"scope": "org", "file_name": "g.md", "content_base64": base64.b64encode(content("gate")).decode(),
            "summary": "DV1 gate probe"}
    n0 = await vcount(conn)
    cv, bv = await api(U_VIEW, "POST", f"{_D}/keys/{K_STAFF}/versions", body)
    cm, bm = await api(U_MEMBER, "POST", f"{_D}/keys/{K_STAFF}/versions", body)
    n1 = await vcount(conn)
    check(bool(cv == 403 and cm == 403 and "manage_disclosures" in dumps(bv) and n1 == n0),
          "a viewer and a member are refused the upload (403 naming manage_disclosures); nothing written",
          f"{cv} {dumps(bv)} {cm} {n0}->{n1}")
    ca, ba = await api(U_A1, "POST", f"{_D}/keys/{K_STAFF}/versions", body | {"content_base64": base64.b64encode(
        content("gate ok")).decode()})
    check(ca == 201, "the same upload by an org admin is admitted (201)", f"{ca} {dumps(ba)}")
    S["v"]["STAFF_ORG_DRAFT"] = (ba.get("version") or {}).get("id")
    plat = {"scope": "platform", "file_name": "p.md", "content_base64": base64.b64encode(content("plat")).decode(),
            "summary": "DV1 staff platform"}
    n0 = await vcount(conn)
    cp, bp = await api(U_A1, "POST", f"{_D}/keys/{K_STAFF}/versions", plat)
    check(bool(cp == 403 and await vcount(conn) == n0), "an org admin may not upload a PLATFORM default (403, nothing "
          "written)", f"{cp} {dumps(bp)}")
    cs, bs = await api(U_SUPER1, "POST", f"{_D}/keys/{K_STAFF}/versions", plat)
    check(cs == 201, "...the same request by a Super Admin is admitted", f"{cs} {dumps(bs)}")
    S["v"]["STAFF_P"] = (bs.get("version") or {}).get("id")
    cl, _ = await api(U_MEMBER, "GET", _D)
    cl2, l2 = await api(U_VIEW, "GET", _D)
    check(bool(cl == 403 and cl2 == 200 and l2.get("rows")), "the list: a member is refused (403), a viewer reads it "
          "(non-empty)", f"{cl} {cl2}")
    perms, voc = l2.get("permissions") or {}, l2.get("vocabularies") or {}
    check(bool(perms.get("can_write") is False and voc.get("editable") == [] and voc.get("inline_editable") == []
               and l2.get("actions") == []),
          "the viewer's envelope publishes can_write false, editable [] and no actions (the page renders no control)",
          f"{dumps(perms)} {dumps(voc.get('editable'))}")


async def y_maker_checker(conn):
    section("[Y] Maker-checker — the uploader cannot publish; a second person can (API + the database CHECK); audited")
    o1 = S["v"].get("O1")
    c, b = await publish(U_A1, o1)
    r = await version_row(conn, o1)
    check(bool(c == 403 and r and r["status"] == "draft" and r["published_by"] is None),
          "the uploader's own publish is refused (403) and the version is still a draft (re-read)", f"{c} {dumps(b)}")
    c, b = await publish(U_A2, o1)
    r = await version_row(conn, o1)
    arch = await platform(conn, "SELECT status, system_to FROM public.disclosure_versions WHERE archived_from_id = $1",
                          o1)
    check(bool(c == 200 and r and r["status"] == "published" and r["published_by"] == U_A2 and r["id"] == UUID(o1)
               and len(arch) == 1 and arch[0]["status"] == "draft" and arch[0]["system_to"] is not None),
          "a second org admin publishes it: the id is KEPT, the prior state archived on the system axis",
          f"{c} {dumps(b)} {[dict(a) for a in arch]}")
    for name in ("O2", "H1", "A1"):
        c, b = await publish(U_A2, S["v"].get(name))
        check(c == 200, f"{name} published by the second person", f"{c} {dumps(b)}")
    p1 = S["v"].get("P1")
    c1, _ = await publish(U_SUPER1, p1)
    c2, _ = await publish(U_A2, p1)
    c3, b3 = await publish(U_SUPER2, p1)
    check(bool(c1 == 403 and c2 == 403 and c3 == 200 and (b3.get("version") or {}).get("published_by") == str(U_SUPER2)),
          "a platform default: its uploader (Super Admin) refused, an org admin refused, a SECOND Super Admin publishes",
          f"{c1} {c2} {c3}")
    sp = S["v"].get("STAFF_P")
    c4, _ = await publish(U_SUPER2, sp)
    check(c4 == 200, "the staff-only platform fixture published by the second Super Admin", f"{c4}")
    op = await operator_conn()
    try:
        draft = S["v"].get("A2_DRAFT")
        tr = op.transaction()
        await tr.start()
        try:
            await op.execute("SELECT set_config('execution.teardown', '', true)")
            await op.execute("UPDATE public.disclosure_versions SET status = 'published', published_by = drafted_by, "
                             "published_at = now() WHERE id = $1 AND system_to IS NULL", UUID(draft))
            refused = None
        except asyncpg.CheckViolationError as exc:
            refused = exc.constraint_name
        finally:
            await tr.rollback()
        check(refused == "disclosure_versions_maker_checker_chk", "the DATABASE refuses publishing by the drafter "
              "even outside the app (CHECK disclosure_versions_maker_checker_chk)", f"{refused}")
        tr = op.transaction()
        await tr.start()
        try:
            await op.execute("UPDATE public.disclosure_versions SET summary = 'tampered' WHERE id = $1 AND system_to IS "
                             "NULL", UUID(o1))
            guarded = False
        except asyncpg.InsufficientPrivilegeError:
            guarded = True
        finally:
            await tr.rollback()
        check(guarded, "a published version's summary cannot be edited in place (the guard trigger, 42501)")
    finally:
        await op.close()
    n = await platform(conn, "SELECT count(*) FROM audit_log WHERE org_id = ANY($1::uuid[]) AND action = "
                             "'disclosure.version_published' AND resource_id = ANY($2::uuid[])", FIXTURE_ORGS,
                       [UUID(S["v"][k]) for k in ("O1", "O2", "H1", "A1", "P1")], fetch="fetchval")
    check(n == 5, "each publish is audited once (counted by fixture version ids)", f"{n}")


async def resolve_as(conn, org, key, **kw):
    return await in_org(conn, org, lambda c: DL.resolve(c, key, org_id=org, **kw))


async def y_resolver(conn):
    section("[Y] Resolver — platform default; org > platform; household > org; account > household; a draft never "
            "shows; a retired version stops on its date; the as-of date picks the version in force")
    d0 = S["seed_day"]
    v = S["v"]
    cases = [
        ("org B (no override) → the platform default", ORG_B, {}, None, v["P1"], "platform"),
        ("org A → its newest org override in force (O2, effective −20)", ORG_A, {}, None, v["O2"], "org"),
        ("org A as of −25 → O1 (O2 not yet effective)", ORG_A, {}, d0 - timedelta(days=25), v["O1"], "org"),
        ("org A as of −35 → the platform default (no org version yet)", ORG_A, {}, d0 - timedelta(days=35), v["P1"],
         "platform"),
        ("household HH_A1 → its override (household > org)", ORG_A, {"household_id": HH_A1}, None, v["H1"],
         "household"),
        ("account ACC_A1 → its override (account > household)", ORG_A, {"account_id": ACC_A1}, None, v["A1"], "account"),
        ("account ACC_A2 (only a DRAFT) → its household's override, read from the account", ORG_A,
         {"account_id": ACC_A2}, None, v["H1"], "household"),
        ("account ACC_A3 (household HH_A2, no override) → the org override", ORG_A, {"account_id": ACC_A3}, None,
         v["O2"], "org"),
        ("household HH_A2 → the org override", ORG_A, {"household_id": HH_A2}, None, v["O2"], "org"),
    ]
    for label, org, kw, as_of, want, scope in cases:
        r = await resolve_as(conn, org, K_ALPHA, as_of=as_of, **kw)
        check(bool(r["version_id"] == want and r["scope"] == scope and r["source"] == "library"),
              f"resolver: {label}", f"{dumps(r)[:300]}")
    # independent SQL for the org-level case: the version table read directly, the rule applied by hand
    rows = await scoped(conn, ORG_A, "SELECT v.id, v.scope, v.effective_from, v.version_no FROM public.disclosure_versions v "
                                     "JOIN public.disclosures d ON d.id = v.disclosure_id WHERE d.key = $1 AND "
                                     "v.system_to IS NULL AND v.status = 'published' AND v.scope IN ('platform', "
                                     "'org')", K_ALPHA)
    best = max(rows, key=lambda x: ({"platform": 1, "org": 2}[x["scope"]], x["effective_from"], x["version_no"]))
    check(bool(rows and str(best["id"]) == v["O2"]), "independent SQL agrees: the most specific, latest effective "
          "published version for org A is O2", f"{[dict(x) for x in rows]}")
    c, b = await api(U_VIEW, "GET", f"{_D}/resolve?keys={K_ALPHA}&account_id={ACC_A1}")
    check(bool(c == 200 and ((b.get("resolved") or {}).get(K_ALPHA) or {}).get("version_id") == v["A1"]),
          "the API's resolve (as a viewer) returns the same account override", f"{c} {dumps(b)[:300]}")
    # a draft never shows: the ACC_A2 draft is not what ACC_A2 resolves to (above); and a key with ONLY drafts → none
    c, b, _ = await upload(U_A1, K_MEMBER, "org", "DV1 member org DRAFT", eff=d0 - timedelta(days=40))
    S["v"]["M_ORG_DRAFT"] = (b.get("version") or {}).get("id")
    r = await resolve_as(conn, ORG_A, K_MEMBER)
    check(bool(c == 201 and r["source"] == "none" and r["version_id"] is None),
          "a key whose only version is a draft resolves to nothing in force (a draft never shows)", f"{dumps(r)[:200]}")
    # retire O2 two days ago: in force before that date, not after
    cr, br = await api(U_A1, "POST", f"{_D}/versions/{v['O2']}/retire", {"reason": "DV1 retire probe",
                                                                         "retire_on": (d0 - timedelta(days=2))
                                                                         .isoformat()})
    after = await resolve_as(conn, ORG_A, K_ALPHA)
    before = await resolve_as(conn, ORG_A, K_ALPHA, as_of=d0 - timedelta(days=5))
    row = await version_row(conn, v["O2"])
    check(bool(cr == 200 and row and row["status"] == "retired" and row["effective_to"] == d0 - timedelta(days=2)
               and after["version_id"] == v["O1"] and before["version_id"] == v["O2"]),
          "retired on −2: today org A falls back to O1; as of −5 it was still O2 (the retire date stops it)",
          f"{cr} {dumps(br)[:200]} after {after['version_id']} before {before['version_id']}")
    c2, b2 = await api(U_A1, "POST", f"{_D}/versions/{v['O2']}/retire", {"reason": "again"})
    check(c2 == 409, "a retired version is not retired again (409)", f"{c2} {dumps(b2)}")


async def y_isolation(conn):
    section("[Y] Org isolation, both directions (rolbypassrls = false) — rows, the API and the stored files")
    c, b, data = await upload(U_B, K_ALPHA, "org", "DV1 org B override", eff=S["seed_day"] - timedelta(days=10))
    S["v"]["B1"] = (b.get("version") or {}).get("id")
    S["b_data"] = data
    a_rows = await scoped(conn, ORG_A, "SELECT org_id FROM public.disclosure_versions WHERE org_id = ANY($1::uuid[]) "
                                       "OR (org_id IS NULL AND drafted_by = ANY($2::uuid[]))", FIXTURE_ORGS, list(USERS))
    b_rows = await scoped(conn, ORG_B, "SELECT org_id FROM public.disclosure_versions WHERE org_id = ANY($1::uuid[]) "
                                       "OR (org_id IS NULL AND drafted_by = ANY($2::uuid[]))", FIXTURE_ORGS, list(USERS))
    check(bool(c == 201 and a_rows and {r["org_id"] for r in a_rows} <= {ORG_A, None}
               and any(r["org_id"] == ORG_A for r in a_rows)),
          "org A's context reads only org A's versions + platform defaults (non-empty)",
          f"{sorted({str(r['org_id']) for r in a_rows})}")
    check(bool(b_rows and {r["org_id"] for r in b_rows} <= {ORG_B, None} and any(r["org_id"] == ORG_B for r in b_rows)),
          "org B's context reads only org B's versions + platform defaults (non-empty)",
          f"{sorted({str(r['org_id']) for r in b_rows})}")
    none = await scoped(conn, "", "SELECT count(*) FROM public.disclosure_versions WHERE org_id = ANY($1::uuid[])",
                        FIXTURE_ORGS, fetch="fetchval")
    check(none == 0, "with no org context no org version is readable (the policy is really enforced)", f"{none}")
    o1 = S["v"]["O1"]
    reads0 = len(GET_LOG)
    cb, _raw, _ = await api_raw(U_B, "GET", f"{_D}/versions/{o1}/file")
    ca, _raw2, _ = await api_raw(U_A1, "GET", f"{_D}/versions/{S['v']['B1']}/file")
    check(bool(cb == 404 and ca == 404 and len(GET_LOG) == reads0),
          "org B cannot read org A's file, nor A B's (404 both ways — refused before any storage read)",
          f"B→A {cb} A→B {ca} reads {reads0}->{len(GET_LOG)}")
    cp, bp = await publish(U_B, S["v"]["A2_DRAFT"])
    cr, br = await api(U_B, "POST", f"{_D}/versions/{o1}/retire", {"reason": "cross-org probe"})
    r = await version_row(conn, o1)
    check(bool(cp == 404 and cr == 404 and r and r["status"] == "published"),
          "org B's admin cannot publish or retire org A's versions (404 on valid bodies; unchanged)",
          f"{cp} {dumps(bp)} {cr} {dumps(br)}")
    ck, bk = await api(U_B, "GET", f"{_D}/keys/{K_ALPHA}")
    ids = {x.get("id") for x in bk.get("versions") or []}
    check(bool(ck == 200 and ids and S["v"]["B1"] in ids and not ({o1, S["v"]["O2"], S["v"]["H1"], S["v"]["A1"]} & ids)),
          "org B's key view lists its own + platform versions, none of org A's", f"{ck} {sorted(ids)}")
    ch, _ = await api(U_B, "GET", f"{_D}/households/{HH_A1}")
    check(ch == 404, "org B cannot open org A's household disclosure panel (404)", f"{ch}")


async def y_files(conn):
    section("[Y] Reads through the app (proxied): bytes and type exact; a hash mismatch is refused")
    c, raw, hdr = await api_raw(U_VIEW, "GET", f"{_D}/versions/{S['v']['H1']}/file")
    row = await version_row(conn, S["v"]["H1"])
    check(bool(c == 200 and row and raw == BLOBS.get(row["r2_key"]) and hdr.get("content-type", "").startswith(
               "application/pdf") and hdr.get("x-content-sha256") == row["content_sha256"]),
          "a viewer reads the household override's file: the exact stored bytes, application/pdf, its hash",
          f"{c} {hdr.get('content-type')}")
    c2, raw2, _ = await api_raw(U_VIEW, "GET", f"{_D}/versions/{S['v']['P1']}/file")
    check(c2 == 200 and bool(raw2), "...and a platform default's file", f"{c2}")
    key = row["r2_key"] if row else ""
    keep = BLOBS.get(key)
    BLOBS[key] = b"tampered"
    try:
        c3, raw3, _ = await api_raw(U_VIEW, "GET", f"{_D}/versions/{S['v']['H1']}/file")
    finally:
        BLOBS[key] = keep
    check(c3 == 409, "a stored file that no longer matches its hash is refused (409), never shown", f"{c3} {raw3[:120]}")


async def y_member(conn):
    section("[Y] Members read only PUBLISHED, member-audience disclosures that apply to them")
    d0 = S["seed_day"]
    v = S["v"]
    steps = [("M_P", U_SUPER1, U_SUPER2, "platform", {}), ("M_A1", U_A1, U_A2, "account", {"acct": ACC_A1}),
             ("M_H2", U_A1, U_A2, "household", {"hh": HH_A2})]
    for name, maker, checker, scope, kw in steps:
        c, b, _ = await upload(maker, K_MEMBER, scope, f"DV1 member {name}", eff=d0 - timedelta(days=40), **kw)
        v[name] = (b.get("version") or {}).get("id")
        cp, _ = await publish(checker, v[name])
        check(bool(c == 201 and cp == 200), f"member fixture {name} ({scope}) uploaded and published", f"{c} {cp}")
    c, b, _ = await upload(U_A1, K_MEMBER, "account", "DV1 member draft ACC_A2", eff=d0 - timedelta(days=40),
                           acct=ACC_A2)
    v["M_D"] = (b.get("version") or {}).get("id")
    cm, mine = await api(U_MEMBER, "GET", f"{_D}/mine")
    rows = mine.get("rows") or []
    by_key = {k: {r.get("version_id") for r in rows if r.get("key") == k} for k in FIXTURE_KEYS}
    check(bool(cm == 200 and by_key[K_MEMBER] == {v["M_P"], v["M_A1"]}),
          "/mine (member key): the platform default and the override on the member's OWN account — not another "
          "family's household override (M_H2), not a draft (M_D, the org draft)",
          f"{cm} got {sorted(x for x in by_key[K_MEMBER] if x)} want {sorted([v['M_P'], v['M_A1']])}")
    check(bool(by_key[K_ALPHA] == {v["O1"], v["H1"], v["A1"]}),
          "/mine (both-audience key): exactly what is in force for the org, the member's household and accounts "
          "(O1 since O2 retired; H1; A1) — never O2 (retired) or another org's B1",
          f"{sorted(x for x in by_key[K_ALPHA] if x)}")
    check(by_key[K_STAFF] == set(), "/mine never lists a staff-only key", f"{by_key[K_STAFF]}")
    check(bool((mine.get("permissions") or {}).get("can_write") is False
               and (mine.get("vocabularies") or {}).get("editable") == []),
          "the member's envelope carries can_write false and editable []", f"{dumps(mine.get('permissions'))}")
    ok, _, _ = await api_raw(U_MEMBER, "GET", f"{_D}/versions/{v['M_A1']}/file")
    refused = [(n, (await api_raw(U_MEMBER, "GET", f"{_D}/versions/{v[n]}/file"))[0])
               for n in ("M_H2", "M_D", "STAFF_P", "O2", "B1")]
    check(bool(ok == 200 and all(c == 404 for _, c in refused)),
          "the member reads the file of a disclosure that applies (200) and gets 404 for another family's override, a "
          "draft, a staff-only default, a retired version and another org's version", f"{ok} {refused}")


async def y_acks(conn):
    section("[Y] Acknowledgements by VERSION (id + hash); stale versions refused; re-acknowledge after a new one")
    v = S["v"]
    body = {"version_id": v["M_A1"], "subject_type": "account", "subject_id": str(ACC_A1),
            "account_id": str(ACC_A1), "screen": "verify.member"}
    c, b = await api(U_MEMBER, "POST", f"{_D}/acknowledgements", body)
    row = await version_row(conn, v["M_A1"])
    a = b.get("acknowledgement") or {}
    check(bool(c == 201 and a.get("version_id") == v["M_A1"] and row and a.get("content_sha256") == row["content_sha256"]),
          "the member acknowledges the account's version in force: the record names its id AND hash",
          f"{c} {dumps(b)}")
    n0 = await platform(conn, "SELECT count(*) FROM disclosure_acknowledgements WHERE user_id = $1", U_MEMBER,
                        fetch="fetchval")
    c2, b2 = await api(U_MEMBER, "POST", f"{_D}/acknowledgements", body)
    n1 = await platform(conn, "SELECT count(*) FROM disclosure_acknowledgements WHERE user_id = $1", U_MEMBER,
                        fetch="fetchval")
    check(bool(c2 == 201 and (b2.get("acknowledgement") or {}).get("id") == a.get("id") and n0 == n1 == 1),
          "acknowledging the same version again records nothing new (one row)", f"{n0}->{n1}")
    c3, b3 = await api(U_MEMBER, "POST", f"{_D}/acknowledgements", body | {"version_id": v["M_P"]})
    check(c3 == 409, "a version that is NOT the one in force for the subject is refused (409)", f"{c3} {dumps(b3)}")
    op = await operator_conn()
    try:
        tr = op.transaction()
        await tr.start()
        try:
            await op.execute(
                "INSERT INTO public.disclosure_acknowledgements (org_id, disclosure_id, version_id, content_sha256, "
                "user_id, subject_type, screen) SELECT org_id, disclosure_id, id, $2, $3, 'account', 'tamper' FROM "
                "public.disclosure_versions WHERE id = $1 AND system_to IS NULL", UUID(v["M_A1"]), "0" * 64, U_MEMBER)
            fk = None
        except asyncpg.ForeignKeyViolationError as exc:
            fk = exc.constraint_name
        finally:
            await tr.rollback()
    finally:
        await op.close()
    check(fk == "disclosure_ack_version_fk", "the database refuses an acknowledgement whose hash is not the version's "
          "(composite FK)", f"{fk}")
    # ── rep13 long/short: the submit gate reads the version in force NOW (org override of the real key, org A) ──
    six = "\n".join(f"- [{k}] DV1 v1 {k}" for k in (*LS.R.RISK_KEYS, "short_unlimited_loss"))
    c, b, _ = await upload(U_A1, REAL_KEY, "org", six, eff=S["seed_day"] - timedelta(days=1))
    v["LS1"] = (b.get("version") or {}).get("id")
    cp, _ = await publish(U_A2, v["LS1"])
    check(bool(c == 201 and cp == 200), "a long_short.risks ORG override (org A only) uploaded and published",
          f"{c} {cp} {dumps(b)[:200]}")
    p = {"id": str(PROPOSAL), "account_id": str(ACC_A1), "maker_id": U_A1, "maker_kind": "user", "status": "draft"}
    real_details_of = LS.details_of
    if list(inspect.signature(real_details_of).parameters) != ["conn", "proposal_id"]:
        raise RuntimeError(f"LS.details_of signature drifted: {inspect.signature(real_details_of)}")

    async def stub_details_of(conn_, proposal_id):     # rep13's own acknowledgement exists (the maker's)
        return {"acknowledged_by": U_A1, "proposal_id": proposal_id} if str(proposal_id) == str(PROPOSAL) else None

    LS.details_of = stub_details_of
    try:
        st = await in_org(conn, ORG_A, lambda c_: LS.disclosure_state(c_, ORG_A, p))
        keys = [x["key"] for x in st["items"]]
        check(bool(st["required"] is True and st["current"] is False and st["acknowledged"] is None
                   and st["resolved"]["version_id"] == v["LS1"] and "short_unlimited_loss" in keys
                   and set(LS.R.RISK_KEYS) <= set(keys) and keys[-3:] == list(LS.ACK_EXTRA)),
              "the proposal's acknowledgement items are the IN-FORCE library risks (+ borrow / margin / summary)",
              f"{dumps(st)[:400]}")
        refused = await _presubmit_refusal(conn, p)
        check(bool(refused and "acknowledge" in refused), "submit is refused until the in-force long_short.risks "
              "version is acknowledged (rep13's own acknowledgement alone no longer suffices)", f"{refused}")
        await in_org(conn, ORG_A, lambda c_: DL.acknowledge(c_, org_id=ORG_A, user_id=U_A1, resolved=st["resolved"],
                                                            subject=LS._subject(p), screen=LS.ACK_SCREEN))
        ok = await _presubmit_refusal(conn, p)
        check(ok is None, "after acknowledging THAT version, submit passes the gate", f"{ok}")
        c, b, _ = await upload(U_A1, REAL_KEY, "org", six.replace("DV1 v1", "DV1 v2"), eff=S["seed_day"])
        v["LS2"] = (b.get("version") or {}).get("id")
        cp, _ = await publish(U_A2, v["LS2"])
        stale = await _presubmit_refusal(conn, p)
        why = await in_org(conn, ORG_A, lambda c_: LS._library_refusal(c_, ORG_A, p))
        check(bool(c == 201 and cp == 200 and stale and "newer version" in stale and why and "newer version" in why),
              "a NEW version is published: the old acknowledgement no longer suffices (submit and approval refuse, "
              "naming the newer version)", f"{stale} / {why}")
        st2 = await in_org(conn, ORG_A, lambda c_: LS.disclosure_state(c_, ORG_A, p))
        await in_org(conn, ORG_A, lambda c_: DL.acknowledge(c_, org_id=ORG_A, user_id=U_A1, resolved=st2["resolved"],
                                                            subject=LS._subject(p), screen=LS.ACK_SCREEN))
        again = await _presubmit_refusal(conn, p)
        acks = await scoped(conn, ORG_A, "SELECT version_id FROM disclosure_acknowledgements WHERE subject_id = $1 "
                                         "ORDER BY acknowledged_at", PROPOSAL)
        check(bool(again is None and [str(r["version_id"]) for r in acks] == [v["LS1"], v["LS2"]]),
              "re-acknowledged: submit passes; both acknowledgements are kept, each naming its own version",
              f"{again} {[str(r['version_id']) for r in acks]}")
        other = await in_org(conn, ORG_B, lambda c_: DL.resolve(c_, REAL_KEY, org_id=ORG_B))
        check(other["version_id"] not in (v["LS1"], v["LS2"]), "org A's long_short.risks override never reaches org B",
              f"{other['version_id']}")
    finally:
        LS.details_of = real_details_of


async def _presubmit_refusal(conn, p) -> str | None:
    try:
        await in_org(conn, ORG_A, lambda c_: LS.presubmit(c_, ORG_A, p))
        return None
    except LS.LongShortConflict as exc:
        return str(exc)


class _BrokenConn:
    """A connection whose every query fails — the library unreadable (storage / database unavailable)."""

    def transaction(self):
        class _T:
            async def __aenter__(self_):
                return self_

            async def __aexit__(self_, *a):
                return False
        return _T()

    async def fetch(self, *a, **k):
        raise ConnectionError("verify: the library is unavailable")

    fetchrow = fetchval = execute = fetch


async def y_migration(conn):
    section("[Y] Migration — every Task 1a disclosure resolves to its key; offline the text is EXACTLY the old text")
    for key in MIGRATED:
        want = old_text(key)
        r = await resolve_as(conn, ORG_B, key)
        lib = r["source"] == "library"
        check(bool(r["key"] == key and r["text"] and (lib or r["text"] == want)),
              f"{key} resolves through the library key (org B: {r['source']}) — never blank",
              f"{dumps(r)[:240]}")
        broken = await DL.resolve_safe(_BrokenConn(), key, org_id=ORG_B)
        check(bool(broken["text"] == want and broken.get("degraded") is True and broken["source"] == "fallback"),
              f"{key}: with the library unavailable the screen text is EXACTLY the pre-library text (BEFORE_REF)",
              f"{broken['text']!r}")
    for key in BRIEF_KEYS:
        r = await resolve_as(conn, ORG_B, key)
        check(bool(r["key"] == key and r["title"] and r["source"] in ("library", "fallback", "none")),
              f"{key} is a library key ({r['source']})", f"{dumps(r)[:200]}")
    # the wired call sites, offline: replica text, the hypothetical gate, the tax label
    want_rep = old_text("replica.fund_affiliation").replace("{ticker}", "DV1T")
    got_rep = T.disclosure_for("dv1t", await DL.text_for(_BrokenConn(), "replica.fund_affiliation", org_id=ORG_B))
    check(got_rep == want_rep, "the fund-replica disclosure offline equals the old disclosure_for output exactly",
          f"{got_rep!r} vs {want_rep!r}")
    g_none = await in_org(conn, ORG_B, lambda c_: HP.gate_for(c_, ORG_B, None, loaded=True))
    check(bool(g_none["disclosure"] == old_text("hypothetical.performance") and g_none["members_allowed"] is False
               and g_none["audience"] == "staff_only"),
          "hypothetical: with no approved policy the gate is unchanged (staff only, the exact NO_POLICY line)",
          f"{dumps(g_none)}")
    pol = {"id": str(_id(91)), "version_no": 1, "audience": "staff_and_members", "disclosure_text": "DV1 firm text"}
    g_b = await in_org(conn, ORG_B, lambda c_: HP.gate_for(c_, ORG_B, pol, loaded=True))
    check(bool(g_b["disclosure"] == "DV1 firm text" and g_b["disclosure_source"] == "policy"),
          "hypothetical: an approved policy's own text stands when the org has no library override (a platform "
          "default never replaces it)", f"{dumps(g_b)}")
    c, b, _ = await upload(U_A1, "hypothetical.performance", "org", "DV1 org hypothetical text",
                           eff=S["seed_day"] - timedelta(days=1))
    v = (b.get("version") or {}).get("id")
    cp, _ = await publish(U_A2, v)
    g_a = await in_org(conn, ORG_A, lambda c_: HP.gate_for(c_, ORG_A, pol, loaded=True))
    g_a0 = await in_org(conn, ORG_A, lambda c_: HP.gate_for(c_, ORG_A, None, loaded=True))
    check(bool(c == 201 and cp == 200 and g_a["disclosure"] == "DV1 org hypothetical text"
               and g_a["disclosure_source"] == "library" and g_a["members_allowed"] is True
               and g_a0["disclosure"] == old_text("hypothetical.performance") and g_a0["members_allowed"] is False),
          "hypothetical: an ORG library version replaces the policy text; the Marketing Rule gate itself is "
          "unchanged (no policy → staff only, NO_POLICY)", f"{dumps(g_a)} / {dumps(g_a0)}")
    tl = await api(U_A1, "GET", "/api/v1/tax/overview")
    note = (tl[1].get("estimate_disclosure") or {})
    check(bool(tl[0] == 200 and note.get("key") == "tax.estimate_label" and note.get("text")),
          "the tax overview carries tax.estimate_label from the resolver (never blank)", f"{tl[0]} {dumps(note)}")
    n0 = await counts(conn)
    code, out, err = run_cmd([sys.executable, str(HERE / "load_disclosure_defaults.py"), "--dry-run"], REPO, timeout=600)
    n1 = await counts(conn)
    plans = [ln for ln in out.splitlines() if ln.startswith("[PLAN] ")]
    check(bool(code == 0 and "DRY RUN" in out and "nothing written, nothing uploaded" in out
               and {p.split(":")[0][7:] for p in plans} == set(DL.KEYS) - DL.NO_PLATFORM_DEFAULT
               and n0 == n1),
          "the operator loader's --dry-run validates every default, plans each R2 key, writes nothing",
          f"{code} {(out + err)[-400:]}")
    check(all("disclosures/platform/" in p for p in plans), "...each planned object is under disclosures/platform/")


async def y_record_panel(conn):
    section("[Y] Override from the household / account page (the record panel)")
    c, b = await api(U_A1, "GET", f"{_D}/households/{HH_A1}")
    row = next((r for r in b.get("rows") or [] if r.get("key") == K_ALPHA), {}) if c == 200 else {}
    check(bool(c == 200 and (row.get("in_force") or {}).get("version_id") == S["v"]["H1"]
               and row.get("why") == "Household override" and [o.get("id") for o in row.get("overrides") or []]
               == [S["v"]["H1"]] and "upload_override" in (b.get("actions") or [])),
          "the household panel shows what is in force (its override, and why) and offers Upload override",
          f"{c} {dumps(row)[:300]}")
    cv, bv = await api(U_VIEW, "GET", f"{_D}/accounts/{ACC_A1}")
    check(bool(cv == 200 and bv.get("actions") == [] and (bv.get("vocabularies") or {}).get("editable") == []),
          "a viewer's account panel offers no upload (actions [], editable [])", f"{cv} {dumps(bv.get('actions'))}")


async def y_audit(conn):
    section("[Y] audit_log — this run's rows counted BY FIXTURE IDS ONLY")
    rows = await platform(conn, "SELECT action, count(*) AS n FROM audit_log WHERE org_id = ANY($1::uuid[]) AND action "
                                "LIKE 'disclosure.%' GROUP BY 1", FIXTURE_ORGS)
    got = {r["action"]: int(r["n"]) for r in rows}
    check(bool(got.get("disclosure.version_drafted", 0) >= 10 and got.get("disclosure.version_published", 0) >= 8
               and got.get("disclosure.version_retired", 0) >= 1 and got.get("disclosure.acknowledged", 0) >= 3),
          "drafts, publishes, the retire and the acknowledgements are all audited (fixture orgs only)", f"{got}")


async def y_routes(conn):
    section("[Y] Routes — own routes via _verify_self; authz1_routes.py --check CURRENT")
    self_routes(check, find)
    code, out, err = run_cmd([sys.executable, str(HERE / "authz1_routes.py"), "--check"], REPO, timeout=600)
    check(bool(code == 0 and "CURRENT" in out), "authz1_routes.py --check: the route record is CURRENT",
          (out + err)[-200:])
    code, out, err = run_cmd([sys.executable, str(HERE / "visibility1_routes.py"), "--check"], REPO, timeout=600)
    check(bool(code == 0 and "CURRENT" in out), "visibility1_routes.py --check: CURRENT", (out + err)[-200:])


async def y_front_end(conn):
    section("[Y] Front end — node --test tests/ (and the sprint's own file); npm run build")
    code, out, err = run_cmd(["node", "--test", "tests/"], WEB_DIR, timeout=900)
    m = re.search(r"^# fail (\d+)", out, re.M)
    check(bool(code == 0 and m is not None and m.group(1) == "0"), "node --test tests/ passes (0 fail)",
          (out + err)[-300:] if code else "")
    code2, out2, err2 = run_cmd(["node", "--test", NODE_TEST_FILE], WEB_DIR, timeout=300)
    n_tests = len(re.findall(r"^test\(", _read(f"apps/web/{NODE_TEST_FILE}"), re.M))
    mp = re.search(r"^# pass (\d+)", out2, re.M)
    check(bool(code2 == 0 and mp and int(mp.group(1)) == n_tests and n_tests >= 8
               and re.search(r"^# fail 0", out2, re.M)),
          f"the sprint's own node tests ({NODE_TEST_FILE}): every test in the FILE passes (list, versions, upload → "
          "draft, publish by another user, override from a household page)",
          f"{n_tests} in file; {(out2 + err2)[-200:] if code2 else (mp.group(1) if mp else None)}")
    code, out, err = run_cmd(["npm", "run", "build"], WEB_DIR, timeout=1800)
    check(bool(code == 0), "npm run build exits 0", (out + err)[-400:] if code else "")


def regression():
    section("[Y] Regression — verify_rep13.py ONCE, flat, AFTER teardown, VERIFY_NESTED=1, 2700 s")
    env = os.environ | {"VERIFY_NESTED": "1"}
    code, out, err = run_cmd([sys.executable, str(HERE / "verify_rep13.py")], REPO, timeout=2700, env=env)
    nt = VT.nested_total(out, code)
    fails = [ln for ln in out.splitlines() if ln.startswith("[FAIL]")]
    check(bool(nt.ok), "verify_rep13.py: 0 FAIL (its OWN final TOTAL line + exit code)",
          f"{nt.detail()}; {fails[:4]} {err[-200:] if not nt.ok else ''}")


async def run(name, fn, *args):
    try:
        res = fn(*args)
        if asyncio.iscoroutine(res):
            return await res
        return res
    except Exception as exc:  # noqa: BLE001
        check(False, f"{name} raised", f"{type(exc).__name__}: {exc}")
        traceback.print_exc()
        return None


async def main_async() -> int:
    dsn, how = await admin_dsn()
    if not dsn:
        print(f"[FAIL] no working DATABASE_URL: {how}")
        print("TOTAL: 0 PASS, 1 FAIL")
        return 1
    os.environ["DATABASE_URL"] = dsn
    print(f"[INFO] database via {how}; today {today().isoformat()}")
    conn = await connect(dsn)
    install_stubs()
    if VS.CLEANUP_FLAG in sys.argv:
        return await cleanup_only(conn)
    pre = real = None
    S["run_start"] = datetime.now(timezone.utc)
    try:
        await run("pre-teardown", teardown)
        check(await leftovers(conn) == 0, "pre-run cleanup: zero fixture rows before seeding")
        # disclosures1 fix 1: these two crashed the whole run (a surplus query argument) with no traceback — run()
        # names the failure and prints it; without a pre-count the fixture sections are SKIPPED, loudly
        pre = await run("pre-run counts", counts, conn)
        real = await run("real library fingerprints", real_prints, conn)
        if real is not None:
            print(f"[INFO] real library rows before the run: { {k: v[0] for k, v in real.items()} }")
        await run("role", y_role_is_real, conn)
        await run("catalog", y_catalog, conn)
        if pre is None or real is None:
            check(False, "pre-run counts + real fingerprints taken",
                  "every fixture-scoped section SKIPPED — nothing to compare the teardown against")
        else:
            await run("seed", seed, conn)
        if pre is not None and real is not None and S.get("seeded"):
            await run("fixtures", y_fixtures, conn)
            section("[Y] Task 1 — findings 1a–1c")
            await run("task1", self_task1, check, find)
            section("[Y] Pure — fallbacks vs BEFORE_REF, the R2 layout, file types")
            await run("pure", self_pure, check, find)
            await run("r2", y_r2, conn)
            await run("gates", y_gates, conn)
            await run("maker-checker", y_maker_checker, conn)
            await run("resolver", y_resolver, conn)
            await run("isolation", y_isolation, conn)
            await run("files", y_files, conn)
            await run("member", y_member, conn)
            await run("acknowledgements", y_acks, conn)
            await run("migration", y_migration, conn)
            await run("record panel", y_record_panel, conn)
            await run("audit", y_audit, conn)
        elif pre is not None and real is not None:
            check(False, "seed completed", "every fixture-scoped section skipped")
        section("[Y] Static — docs, defaults, regression set, code markers")
        await run("static", self_static, check, find)
        await run("routes", y_routes, conn)
        await run("front end", y_front_end, conn)
        check(not NETWORK_CALLS, "no network call was attempted", f"{NETWORK_CALLS}")
        check(not S3_BUILT, "no real R2 client was ever built (every object went through the fake transport)",
              f"{S3_BUILT}")
    finally:
        section("[Y] Teardown — zero fixture rows; every counted table at its pre-test count; real rows unchanged")
        try:
            await close_pool()
        except Exception:  # noqa: BLE001
            pass
        try:
            log = await teardown()
            print(f"[INFO] teardown removed: {log}")
            left = await leftovers(conn)
            check(left == 0, "zero fixture rows remain (library rows, acknowledgements and audit_log by fixture id "
                  "included)", f"{left}")
            if pre is not None:
                post = await counts(conn)
                for t in COUNTED:
                    check(post[t] == pre[t], f"{t} at its pre-test count (fixture rows)", f"{pre[t]} → {post[t]}")
            if real is not None:
                now = await real_prints(conn)
                for t in real:
                    check(now[t] == real[t], f"real {t} unchanged (count + checksum by id)",
                          f"{real[t][0]} → {now[t][0]} rows")
        except Exception as exc:  # noqa: BLE001
            check(False, "teardown raised", f"{type(exc).__name__}: {exc}")
            traceback.print_exc()
        finally:
            await conn.close()

    if os.environ.get("VERIFY_NESTED") == "1":
        print("[SKIP] nested run")
    else:
        await run("regression", regression)
    print(f"\nTOTAL: {_n_pass} PASS, {_n_fail} FAIL")
    return 1 if _n_fail else 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main_async()))
    except SystemExit:
        raise
    except BaseException as exc:  # noqa: BLE001 — TOTAL on every exit path
        print(f"[FAIL] the verify crashed: {type(exc).__name__}: {exc}")
        traceback.print_exc()
        print(f"\nTOTAL: {_n_pass} PASS, {_n_fail + 1} FAIL")
        sys.exit(1)
