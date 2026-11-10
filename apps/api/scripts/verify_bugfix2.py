"""verify_bugfix2 — member data isolation + the HollisMuse round-3 defects (BUGFIX2).

Pass/fail only, no prompts. Prints 'TOTAL: N PASS, M FAIL' and exits non-zero on any failure.
AUTHZ_TEST_MODE=1 is set before anything imports ``main``; secrets are hydrated from Doppler over HTTPS
(``_db_connect.hydrate``) before ``main`` is imported. No network: the LiteLLM live-info enrichment, the
embedding call and R2 are replaced by local stubs (a real call fails the run).

Everything after the bearer token is the real app (middleware, Principal, every gate, every handler) against
the live database, in TWO fixture orgs (99000000-…-0000000bf201 / bf202). Fixture users carry the auth0_sub
their token carries and ``is_test = true`` (asserted), as do the fixture orgs. Personas in org A: a MEMBER
(delegate of family 1), a second member holding a STAFF-ONLY profile + permission set, an investment-staff
user, an org admin, a super admin; in org B a member. Two families in org A: family 1 (the member's) and
family 2 (another family — the Hargrove shape: an LLC owned 40 % / 60 % by an individual and a trust, with a
compliance record, a note and a document).

Teardown at start and end: the shared scoped cleanup (_verify_cleanup) on the operator DSN, by fixture
org / user ids; then every counted table is re-read for its exact pre-test count, and the REAL rows of
users / entities / deals / profiles / permission sets / roles / grants are fingerprinted (count + md5 of the
primary key from the catalog) before and after — they must be identical. The MUSE rows were removed by
scripts/bugfix2_remove_muse_rows.py (Task 7), never by this script; this script only asserts they are gone.

--self-checks-only: git / route-record / static checks only (no Doppler, DB, network or npm).
--cleanup-only: the teardown + leftover check only.
With VERIFY_NESTED=1 the regression section prints one '[SKIP] nested run'.
"""

from __future__ import annotations

import os as _authz_os
_authz_os.environ["AUTHZ_TEST_MODE"] = "1"  # AUTHZ1: set before main is imported

import asyncio
import importlib.util
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import traceback
from datetime import date
from uuid import UUID

HERE = pathlib.Path(__file__).resolve().parent
API_DIR = HERE.parent
REPO = API_DIR.parents[1]
WEB_DIR = REPO / "apps" / "web"
sys.path.insert(0, str(HERE))

import _verify_cleanup as VC  # noqa: E402  (stdlib only)
import _verify_self as VS  # noqa: E402
import _verify_total as VT  # noqa: E402  (stdlib only)
import _verify_roles as VR  # noqa: E402  — every fixture role is a user_roles grant the app decides as meant
import _verify_scope as VSC  # noqa: E402  (stdlib only) — live-written tables counted by fixture ids

SPRINT_KEY = "bugfix2"
BEFORE_REF = "3d5368a"   # bugfix2-work base (rep11 + previewperf merged, schema refreshed) — the pre-sprint code
ROUTES_JSON = REPO / "docs" / "AUTHZ1_ROUTES.json"
MUSE_MODEL = "a9458f13-c360-4e30-a4db-11eb0366c6cd"
MUSE_WORKFLOW = "8935770a-2cb6-4892-aef9-26a643d75810"

# The 73 READ routes AUTHZ1 left at "none beyond auth" — classified (Task 1a). Read off the BEFORE record; the
# expected class of each is asserted against the AFTER record, never re-derived from it.
STAFF_ONLY_PREFIXES = ("/api/v1/entities/{entity_id}/full", "/api/v1/entities/{entity_id}/notes",
                       "/api/v1/entities/{entity_id}/ownership-graph", "/api/v1/documents/", "/api/v1/records/",
                       "/api/v1/document-link-proposals", "/api/v1/portfolio/precedence", "/api/v1/spvs",
                       "/api/v1/entities/{entity_id}/tree", "/api/v1/entities/{entity_id}/lookthrough",
                       "/api/v1/entities/{entity_id}/relationships", "/api/v1/entity-groups",
                       "/api/v1/entities/{entity_id}/ownership", "/api/v1/ledger/")
STAFF_OR_ORGADMIN = {"/api/v1/admin/model-catalog", "/api/v1/admin/system-one-catalog",
                     "/api/v1/modeling/ta/calibration-floor"}
PUBLIC_TO_ORG = {"/api/v1/config", "/api/v1/taxonomy", "/api/v1/transaction-types", "/api/v1/reference/{list_key}",
                 "/api/v1/fx-rates", "/api/v1/theme", "/api/v1/investment-profile/questions",
                 "/api/v1/market/catalog", "/api/v1/market/series", "/api/v1/deal-room/templates"}


def _class_of(path: str) -> str:
    if path in PUBLIC_TO_ORG:
        return "PUBLIC-TO-ORG"
    if path in STAFF_OR_ORGADMIN:
        return "STAFF-OR-ORG-ADMIN"
    if any(path == p or path.startswith(p.rstrip("/") + "/") or path == p.rstrip("/") for p in STAFF_ONLY_PREFIXES):
        return "STAFF-ONLY"
    return "MEMBER-SCOPED-OR-OWN"


def _open_reads(data: dict) -> dict:
    return {(tuple(r["methods"]), r["path"], r["name"]): r for r in data["routes"]
            if not r["public"] and not r["write"] and r["after"] == "none beyond auth"}


def self_task1(check, find):
    before = json.loads(VS.git_show(BEFORE_REF, "docs/AUTHZ1_ROUTES.json") or "{}")
    end_ref, _how = VS.sprint_end_ref(SPRINT_KEY)
    end_txt = VS.git_show(end_ref, "docs/AUTHZ1_ROUTES.json") if end_ref != "HEAD" else None
    now = json.loads(end_txt) if end_txt else json.loads(ROUTES_JSON.read_text(encoding="utf-8"))
    b = _open_reads(before)
    check(len(b) == 73, "Task 1a: the BEFORE record has exactly 73 non-public READ routes gated by nothing beyond auth",
          f"{len(b)}")
    after = {(tuple(r["methods"]), r["path"], r["name"]): r for r in now.get("routes", [])}
    table, bad = [], []
    for k, r in sorted(b.items(), key=lambda kv: kv[0][1]):
        cls = _class_of(k[1])
        a = after.get(k)
        if a is None:
            # The one moved route: ownership_tree.staff_ownership_graph left the shadowed path (WF-03) — it was
            # never in the 73 (it was already staff); anything else missing is a failure.
            bad.append((k[1], "absent after"))
            continue
        gate = a["after"]
        table.append((k[1], cls, gate, a.get("note", "")[:60]))
        if cls == "STAFF-ONLY" and not (gate == "staff" and a["expect"]["rule"] == "staff"):
            bad.append((k[1], cls, gate))
        elif cls == "STAFF-OR-ORG-ADMIN" and gate != "staff or authz manage_org_settings":
            bad.append((k[1], cls, gate))
        elif cls == "PUBLIC-TO-ORG" and not (gate == "none beyond auth" and a["note"].startswith("PUBLIC-TO-ORG")):
            bad.append((k[1], cls, gate, a["note"]))
        elif cls == "MEMBER-SCOPED-OR-OWN" and not (
                (gate == "none beyond auth" and a["note"].startswith("OWN ROWS"))
                or (a["expect"]["rule"] == "scoped" and gate != "none beyond auth")):
            bad.append((k[1], cls, gate, a["note"]))
    for row in table:
        print(f"    {row[1]:22s} {row[0]:62s} → {row[2]}")
    check(len(table) == 73 and not bad,
          "Task 1a: every one of the 73 is classified and carries its class's gate (staff / staff-or-org-admin / "
          "member scope / own rows / public-to-org with its reason)", f"{len(table)}; mismatches {bad[:6]}")
    classes = {}
    for _p, cls, gate, _n in table:
        key = cls if cls != "MEMBER-SCOPED-OR-OWN" else ("OWN ROWS" if gate == "none beyond auth" else "MEMBER-SCOPED")
        classes[key] = classes.get(key, 0) + 1
    print(f"    [INFO] Task 1a classes: {classes}")
    check(classes == {"STAFF-ONLY": 24, "STAFF-OR-ORG-ADMIN": 3, "PUBLIC-TO-ORG": 10, "OWN ROWS": 18,
                      "MEMBER-SCOPED": 18},
          "Task 1a: the class counts are the reported 24 / 3 / 10 / 18 / 18", f"{classes}")
    unclassified = [r["path"] for r in now.get("routes", []) if "unclassified" in (r.get("note") or "")]
    check(not unclassified, "no open read is left unclassified in the record", f"{unclassified}")
    find("Task 1b: a member's family = delegate_grants (principal_entity_id → delegate_user_id) resolved through "
         "resolve_entity_set subtree + filter_restricted (document_embedding.visible_entity_ids, is_staff=False) — "
         "the engine the clean member portfolio path (routers.portfolio_positions) already uses; reused as "
         "services.authz.member_entity_scope / require_entity_access",
         "the three real member test accounts hold ZERO delegate grants today, so their family is empty")
    find("Task 1c: jpl991722 vs jpl991723 — identical in the DB (role member, same 10 permissions, no profile, no "
         "permission set, no staff assignment, no delegate grant). The API refuses both "
         "(GET /replication/hypothetical-policy needs view_replication); what 'loaded' was the Next page's chrome + "
         "title, rendered for ANY signed-in user before the API answered. Fixed by the /admin area layout",
         "no data difference between the two users exists to explain Muse's observation")
    find("Task 1d: the Compliance tab opened because GET /entities/{id}/full returned compliance_record with NO "
         "permission check — not the 'Compliance Override' set (it grants only view_dashboard; the Principal "
         "never read permission sets). The real staff-only holding is jpl991721's PROFILE 'CSA / Ops' "
         "(staff, manage_documents, view_members, view_workflow_runs), read by the workflow gates")


def _by_name(d: dict) -> dict:
    return {(tuple(r["methods"]), r["path"], r["name"]): r for r in (d or {}).get("routes", [])}


def self_routes(check, find):
    """The sprint's OWN route changes: BEFORE_REF → its end ref (its branch tip once merged; HEAD on its branch),
    keyed by (methods, path, handler) — _verify_self's (methods, path) key collapses the two handlers that share
    /entities/{entity_id}/documents and the formerly shadowed /ownership-graph."""
    end_ref, how = VS.sprint_end_ref(SPRINT_KEY)
    VS._require_own_base(BEFORE_REF, SPRINT_KEY)
    before = _by_name(json.loads(VS.git_show(BEFORE_REF, "docs/AUTHZ1_ROUTES.json") or "{}"))
    end = _by_name(json.loads(VS.git_show(end_ref, "docs/AUTHZ1_ROUTES.json") or "{}"))
    today = _by_name(json.loads(ROUTES_JSON.read_text(encoding="utf-8")))
    where = f"{BEFORE_REF} → {how}"
    changed = [k for k in before if k in end and before[k]["after"] != end[k]["after"]]
    widened = [k for k in changed if before[k]["after"] != "none beyond auth" and end[k]["after"] == "none beyond auth"]
    check(bool(changed) and not widened, "the sprint's own route changes only narrow (no gate became open)",
          f"{where}; {len(changed)} changed; widened {widened[:4]}")
    from_open = [k for k in changed if before[k]["after"] == "none beyond auth" and not before[k]["write"]]
    check(len(from_open) == 45, "45 formerly open READ routes now carry a gate (sprint's own diff)",
          f"{where}; {len(from_open)}")
    new = sorted(k for k in end if k not in before)
    want = {("DELETE", "/api/v1/admin/workflows/{definition_id}", "delete_workflow"),
            ("GET", "/api/v1/entities/{entity_id}/ownership-tree", "staff_ownership_graph")}
    got = {(k[0][0], k[1], k[2]) for k in new}
    check(got == want, "exactly two new routes: the workflow delete and the staff tree's own path", f"{sorted(got)}")
    open_new = [(k, end[k]["after"]) for k in new if end[k]["after"] == "none beyond auth"]
    check(not open_new, "every new route carries a gate", f"{open_new}")
    gone = [k for k in before if k not in end]
    check(gone == [(('GET',), "/api/v1/entities/{entity_id}/ownership-graph", "staff_ownership_graph")],
          "the only removed route record is the shadowed (never reachable) staff tree path", f"{gone}")
    # STAFFROLES1 (a later sprint) re-gated the staff tree from "staff" to "internal" (any internal firm role sees
    # staff data; a member is still refused) — that one documented narrowing-equivalent move is accepted; any other
    # change, and any route that became open, is still drift.
    restaged = {("staff", "internal")}
    drifted = [k for k in new if k not in today or (today[k]["after"] != end[k]["after"]
                                                     and (end[k]["after"], today[k]["after"]) not in restaged)]
    check(not drifted, "the sprint's new routes still exist today with the same gate (or STAFFROLES1's staff → "
          "internal)", f"{drifted}")


def self_static(check, find):
    def read(rel):
        p = REPO / rel
        return p.read_text(encoding="utf-8") if p.exists() else ""

    rules = read("docs/VERIFY_RULES.md")
    check("time the server, not the operator's network" in rules.lower(),
          "Task 6/9: VERIFY_RULES.md states 'time the server, not the operator's network'")
    bfu = read("apps/api/scripts/verify_bulkfetchui.py")
    check('server-side elapsed_ms under' in bfu and "INFO end to end (not asserted)" in bfu
          and "PREVIEW_LIMIT_SECONDS * 1000" in bfu,
          "Task 6: verify_bulkfetchui's preview check asserts the server's elapsed_ms (best of 3) under the SAME "
          "limit and prints end to end as INFO with the round-trip count")
    check(re.search(r"^PREVIEW_LIMIT_SECONDS\s*=\s*2(\.0)?\b", bfu, re.M) is not None,
          "Task 6: the 2 s limit is not raised")
    regs = read("scripts/regression_set.txt")
    check("verify_bugfix2.py" in regs, "Task 9: verify_bugfix2.py is in scripts/regression_set.txt")
    status = read("docs/PROJECT_STATUS.md")
    check("BUGFIX2" in status and "73" in status and "member data isolation" in status.lower(),
          "Task 9: PROJECT_STATUS.md carries the bugfix2 entry with the 1a summary")
    proxy = read("apps/web/app/api/entities/[id]/ownership-graph/route.js")
    check("/ownership-tree" in proxy, "WF-03: the web proxy forwards the staff tree to its own API path")
    for area in ("crm", "spvs", "admin", "portfolio/replication"):
        src = read(f"apps/web/app/{area}/layout.js")
        check(f'area="/{area}"' in src and "AreaGuard" in src,
              f"direct URL: app/{area}/layout.js wraps every page in the server-side AreaGuard")
    page = read("apps/web/app/investment-profile/page.js")
    check('fetchAPI("/api/v1/entities")' in page,
          "ROUTE-01: Investment Profile's entity dropdown is GET /entities (member-scoped server-side)")


def self_checks(check, find):
    self_task1(check, find)
    self_routes(check, find)
    self_static(check, find)


if __name__ == "__main__" and VS.requested():
    sys.exit(VS.run_self_checks(self_checks, SPRINT_KEY, __file__))

# VERIFYFIX1 — TOTAL on every exit path (a crash at import, an early sys.exit, a SIGTERM from `timeout` included).
import _verify_total as VT  # noqa: E402  (stdlib only)
if __name__ == "__main__":
    VT.install_guard(lambda: (globals().get("_n_pass", 0), globals().get("_n_fail", 0)))

from _db_connect import admin_dsn, connect, hydrate, resolve_dsn  # noqa: E402

hydrate()  # BEFORE main is imported (Settings is lru_cached)

import httpx  # noqa: E402

import _authz1_common as C  # noqa: E402

OPERATOR_DSN_VAR = "DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP"
TAG = "bugfix2verify"
ORG_A = UUID("99000000-0000-0000-0000-0000000bf201")
ORG_B = UUID("99000000-0000-0000-0000-0000000bf202")
FIXTURE_ORGS = [ORG_A, ORG_B]
DEFAULT_ORG = UUID("00000000-0000-0000-0000-000000000001")
MISSING = UUID("99000000-0000-0000-0000-0000000bf2ff")


def _id(n: int) -> UUID:
    return UUID(f"99000000-0000-0000-0000-0000000b{n:04d}")


U_SUPER, U_STAFF, U_ORGADMIN, U_MEMBER, U_MEMBER2, U_OTHER = (_id(2110), _id(2111), _id(2112), _id(2113),
                                                               _id(2114), _id(2115))
USERS = {
    U_SUPER: (ORG_A, "super_admin", "super"), U_STAFF: (ORG_A, "member", "staff"),
    U_ORGADMIN: (ORG_A, "org_admin", "orgadmin"), U_MEMBER: (ORG_A, "member", "member"),
    U_MEMBER2: (ORG_A, "member", "member2"), U_OTHER: (ORG_B, "member", "other"),
}
# family 1 (the member's): an individual + a trust it owns; family 2: the Hargrove shape
E_M1, E_M1T, E_F2, E_F2T, E_F2L, E_B = _id(2120), _id(2121), _id(2122), _id(2123), _id(2124), _id(2125)
F2_NAME = "BF2 Family Two"
D_DRAFT, D_REVIEW, D_SUBMITTED, D_ACTIVE, D_B = _id(2130), _id(2131), _id(2132), _id(2133), _id(2134)
DOC_F2, DOC_M1 = _id(2140), _id(2141)
P_STAFFONLY, P_SAFE, PS_STAFFONLY = _id(2150), _id(2151), _id(2152)
WF_NORUN, WFV_NORUN, WF_RUN, WFV_RUN, WFR_RUN = _id(2160), _id(2161), _id(2162), _id(2163), _id(2164)
G_F2 = _id(2170)
V_SPV = _id(2180)   # fix 1: a fixture vehicle for the ledger reads (journal_entries.vehicle_id)
DOC_F2_KEY = f"{TAG}/doc-f2.pdf"   # fix 1: DOC_F2 has a stored file; DOC_M1 has none (storage_key NULL)
LEDGER_VEHICLE_ROUTES = ("/api/v1/ledger/entries", "/api/v1/ledger/trial-balance",
                         "/api/v1/ledger/capital-accounts", "/api/v1/ledger/templates")

COUNTED = ("public.users", "public.organizations", "public.entities", "public.deals", "public.entity_ownership",
           "public.delegate_grants", "public.staff_assignments", "public.compliance_records", "public.entity_notes",
           "public.entity_documents", "public.roles", "public.user_roles", "public.role_permissions",
           "public.profiles", "public.profile_permissions", "public.permission_sets",
           "public.permission_set_permissions", "public.user_permission_sets", "public.workflow_definitions",
           "public.workflow_versions", "public.workflow_runs", "public.member_todos", "public.audit_log",
           "public.entity_groups", "public.entity_group_members", "public.spvs")
# Real rows that must not change (Task 8): fingerprinted before seeding and after teardown.
REAL_FINGERPRINTED = ("public.users", "public.entities", "public.deals", "public.profiles", "public.permission_sets",
                      "public.user_permission_sets", "public.profile_permissions",
                      "public.permission_set_permissions", "public.roles", "public.user_roles",
                      "public.role_permissions", "public.delegate_grants")
# fix 1 (FAIL E): columns real sign-in activity writes during a run — services.users.touch_last_login stamps
# users.last_login_at on every real sign-in (a real user signed in mid-run: only that column moved). Every other
# column of every real users row is still checksummed (role, status, org_id, profile_id, email, is_active …),
# and the count still proves no row was added or removed.
ACTIVITY_COLUMNS = {"public.users": ("last_login_at",)}

_n_pass = 0
_n_fail = 0


def check(passed: bool, label: str, detail: str = "") -> bool:
    assert isinstance(passed, bool), f"check() got {passed!r} ({type(passed).__name__}) for {label!r}"
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


def run_cmd(args, cwd, timeout=600, env=None):
    """Foreground subprocess; never backgrounded."""
    try:
        p = subprocess.run(args, cwd=str(cwd), capture_output=True, text=True, timeout=timeout,
                           env=env or os.environ.copy())
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired as exc:
        return 124, exc.stdout or "", f"timeout after {timeout}s"
    except FileNotFoundError as exc:
        return 127, "", str(exc)


def sub_of(uid: UUID) -> str:
    return f"auth0|{TAG}-{USERS[uid][2]}"


def email_of(uid: UUID) -> str:
    return f"{TAG}-{USERS[uid][2]}@test.local"


def dumps(x) -> str:
    return json.dumps(x, ensure_ascii=False, default=str)[:400]


# ═══ DB helpers (the verify's own connection; RLS context set explicitly on every read) ═══════════════════
async def platform(conn, sql, *args, fetch="fetch"):
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        return await getattr(conn, fetch)(sql, *args)


async def scoped(conn, org, sql, *args, fetch="fetch"):
    async with conn.transaction():
        await conn.execute(
            "SELECT set_config('app.current_org_id', $1, true), set_config('app.is_super_admin', 'false', true),"
            " set_config('app.current_auth0_sub', '', true)", str(org))
        return await getattr(conn, fetch)(sql, *args)


async def counts(conn) -> dict:
    return {t: int(await platform(conn, VSC.count_sql(t, FIXTURE_ORGS, list(USERS)), fetch="fetchval")) for t in COUNTED}


async def pk_of(conn, table: str) -> list[str]:
    rows = await platform(conn, "SELECT a.attname FROM pg_constraint c JOIN pg_attribute a ON a.attrelid = c.conrelid "
                                "AND a.attnum = ANY(c.conkey) WHERE c.conrelid = $1::regclass AND c.contype = 'p' "
                                "ORDER BY array_position(c.conkey, a.attnum)", table)
    return [r["attname"] for r in rows]


def _row_expr(table: str) -> str:
    """The row as jsonb minus the ACTIVITY_COLUMNS of its table (named, never a wildcard)."""
    expr = "to_jsonb(t)"
    for c in ACTIVITY_COLUMNS.get(table, ()):
        expr += f" - '{c}'"
    return expr


async def fingerprint(conn, table: str) -> tuple[int, str]:
    """Count + md5 of every row (minus the table's named ACTIVITY_COLUMNS), keyed by the table's real primary key
    from the catalog."""
    pk = await pk_of(conn, table)
    if not pk:
        raise RuntimeError(f"{table} has no primary key in the catalog")
    key = " || ',' || ".join(f'"{c}"::text' for c in pk)
    row = await platform(conn, f"SELECT count(*) AS n, md5(COALESCE(string_agg(({_row_expr(table)})::text, '|' "
                               f"ORDER BY {key}), '')) AS h FROM {table} t", fetch="fetchrow")
    return int(row["n"]), row["h"]


async def column_hashes(conn, table: str) -> dict:
    """{primary key text: {column: md5(value)}} over EVERY column — diagnostics only: on a fingerprint mismatch the
    changed ids and column NAMES are printed (never a value; no personal data)."""
    pk = await pk_of(conn, table)
    key = " || ',' || ".join(f'"{c}"::text' for c in pk)
    rows = await platform(conn, f"SELECT {key} AS k, (SELECT jsonb_object_agg(e.key, md5(e.value::text)) "
                                f"FROM jsonb_each(to_jsonb(t)) e) AS h FROM {table} t")
    return {r["k"]: json.loads(r["h"]) if isinstance(r["h"], str) else r["h"] for r in rows}


def changed_columns(before: dict, after: dict) -> list[str]:
    out = [f"{k}: row removed" for k in before if k not in after]
    out += [f"{k}: row added" for k in after if k not in before]
    for k in before:
        if k in after:
            cols = sorted(c for c in set(before[k]) | set(after[k]) if before[k].get(c) != after[k].get(c))
            if cols:
                out.append(f"{k}: {','.join(cols)}")
    return out


async def operator_conn():
    dsn, how = await resolve_dsn(OPERATOR_DSN_VAR)
    if not dsn:
        raise RuntimeError(f"no operator DSN for the fixture teardown path: {how}")
    return await connect(dsn)


async def teardown(_conn=None) -> list[str]:
    """Fixture rows only — fixture orgs and fixture users, by id. Never a TRUNCATE."""
    users = list(USERS)
    op = await operator_conn()
    try:
        async with op.transaction():
            await op.execute("SELECT set_config('app.is_super_admin', 'true', true)")
            plan = {
                "public.audit_log": ("org_id = ANY($1::uuid[]) OR user_id = ANY($2::uuid[])", [FIXTURE_ORGS, users]),
                "public.member_todos": ("user_id = ANY($1::uuid[]) OR org_id = ANY($2::uuid[])",
                                        [users, FIXTURE_ORGS]),
                "public.user_permission_sets": ("user_id = ANY($1::uuid[])", [users]),
            }
            res = await VC.cleanup(op, orgs=FIXTURE_ORGS, users=users, user_sub_like=f"%{TAG}%", plan=plan,
                                   label="verify_bugfix2")
        return res["log"]
    finally:
        await op.close()


async def leftovers(conn) -> int:
    n = int(await platform(conn, "SELECT count(*) FROM users WHERE id = ANY($1::uuid[]) OR auth0_sub LIKE $2",
                           list(USERS), f"%{TAG}%", fetch="fetchval"))
    op = await operator_conn()
    try:
        n += await VC.leftover_count(op, orgs=FIXTURE_ORGS, users=list(USERS), user_sub_like=f"%{TAG}%")
    finally:
        await op.close()
    return n


async def cleanup_only(conn) -> int:
    import time as _time

    t0 = _time.monotonic()
    log: list[str] = []
    try:
        log = await teardown(conn)
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


# ═══ Constraints read from the catalog before seeding ══════════════════════════════════════════════════════
async def y_constraints(conn):
    section("[Y] Constraints of every seeded table, read from pg_constraint before seeding")
    tables = ("public.entities", "public.entity_ownership", "public.delegate_grants", "public.deals",
              "public.compliance_records", "public.entity_documents", "public.profiles",
              "public.permission_sets", "public.user_permission_sets", "public.workflow_definitions",
              "public.workflow_versions", "public.workflow_runs", "public.entity_groups",
              "public.entity_group_members", "public.spvs")
    rows = await platform(conn, "SELECT conrelid::regclass::text AS t, contype, conname, pg_get_constraintdef(oid) "
                                "AS def FROM pg_constraint WHERE conrelid = ANY($1::regclass[]) "
                                "AND contype IN ('u', 'c', 'f') ORDER BY 1, 2, 3", list(tables))
    by = {}
    for r in rows:
        by.setdefault(r["t"].replace("public.", ""), []).append(r)
    for t in tables:
        print(f"    {t}: " + "; ".join(f"{r['contype']}:{r['conname']}" for r in by.get(t.replace('public.', ''), [])))
    dg = [r["def"] for r in by.get("delegate_grants", []) if r["contype"] == "c" and "scope" in r["def"]]
    check(not dg or all("view_only" in d for d in dg), "delegate_grants.scope CHECK admits 'view_only' (seeded)",
          f"{dg}")
    ds = await platform(conn, "SELECT array_agg(enumlabel::text) FROM pg_enum WHERE enumtypid = 'deal_status'::regtype",
                        fetch="fetchval")
    check({"draft", "submitted", "under_review", "active"} <= set(ds or []),
          "deal_status enum carries draft / submitted / under_review / active (seeded)", f"{ds}")
    for typ, label in (("kyc_status", "approved"), ("ofac_status", "passed")):
        labels = await platform(conn, "SELECT array_agg(enumlabel::text) FROM pg_enum WHERE enumtypid = $1::regtype",
                                typ, fetch="fetchval")
        check(label in (labels or []), f"{typ} enum carries '{label}' (seeded)", f"{labels}")
    check(len(rows) > 0, "constraints read from the catalog", f"{len(rows)}")


# ═══ Seed ═══════════════════════════════════════════════════════════════════════════════════════════════════
async def seed(conn) -> None:
    from services import rbac

    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        for org, nm in ((ORG_A, "a"), (ORG_B, "b")):
            await conn.execute("INSERT INTO organizations (id, name, slug, is_test) VALUES ($1, $2, $3, true)",
                               org, f"BUGFIX2 Verify Org {nm.upper()}", f"{TAG}-{nm}")
        perm_ids = {r["name"]: r["id"] for r in await conn.fetch("SELECT id, name FROM permissions")}
        roles = {}
        for org, rname in ((ORG_A, "member"), (ORG_A, "investment_staff"), (ORG_A, "super_admin"),
                           (ORG_B, "member")):
            desc, perms = C.BASELINE_ROLES[rname]
            rid = await rbac.ensure_role(conn, org, rname, desc)
            for p in perms:
                if p in perm_ids:
                    await conn.execute("INSERT INTO role_permissions (role_id, permission_id) VALUES ($1, $2) "
                                       "ON CONFLICT DO NOTHING", rid, perm_ids[p])
            roles[(org, rname)] = rid
        for uid, (org, account_role, nm) in USERS.items():
            await conn.execute(
                "INSERT INTO users (id, org_id, email, full_name, auth0_sub, role, status, is_active, is_test) "
                "VALUES ($1, $2, $3, $4, $5, $6, 'active', true, true)",
                uid, org, email_of(uid), f"BUGFIX2 {nm.title()}", sub_of(uid), account_role)
        for uid, rname in ((U_SUPER, "super_admin"), (U_STAFF, "investment_staff"), (U_MEMBER, "member"),
                           (U_MEMBER2, "member")):
            await conn.execute("INSERT INTO user_roles (user_id, role_id) VALUES ($1, $2)", uid, roles[(ORG_A, rname)])
        await conn.execute("INSERT INTO user_roles (user_id, role_id) VALUES ($1, $2)", U_OTHER,
                           roles[(ORG_B, "member")])
        await rbac.grant_org_admin(conn, U_ORGADMIN, ORG_A)

        for eid, org, etype, name in ((E_M1, ORG_A, "individual", "BF2 Member One"),
                                      (E_M1T, ORG_A, "trust", "BF2 Member One Trust"),
                                      (E_F2, ORG_A, "individual", F2_NAME),
                                      (E_F2T, ORG_A, "trust", "BF2 Family Two Trust"),
                                      (E_F2L, ORG_A, "llc", "BF2 Family Two Capital LLC"),
                                      (E_B, ORG_B, "individual", "BF2 Org B Person")):
            await conn.execute("INSERT INTO entities (id, org_id, entity_type, display_name) "
                               "VALUES ($1, $2, $3::entity_type, $4)", eid, org, etype, name)
        # family 1: the member's individual owns its trust (relationship edge — the subtree engine's table)
        await conn.execute("INSERT INTO entity_relationships (org_id, from_entity_id, to_entity_id, relationship_type, "
                           "ownership_pct) VALUES ($1, $2, $3, 'ownership', 100)", ORG_A, E_M1, E_M1T)
        # family 2: the Hargrove shape, in entity_ownership (the seed table the tree used to ignore)
        await conn.execute("INSERT INTO entity_ownership (org_id, parent_id, child_id, ownership_pct) VALUES "
                           "($1, $2, $4, 40), ($1, $3, $4, 60)", ORG_A, E_F2, E_F2T, E_F2L)
        await conn.execute("INSERT INTO delegate_grants (org_id, principal_entity_id, delegate_user_id, scope) "
                           "VALUES ($1, $2, $3, 'view_only')", ORG_A, E_M1, U_MEMBER)
        await conn.execute("INSERT INTO delegate_grants (org_id, principal_entity_id, delegate_user_id, scope) "
                           "VALUES ($1, $2, $3, 'view_only')", ORG_B, E_B, U_OTHER)
        for eid in (E_M1, E_M1T, E_F2, E_F2T, E_F2L):
            await conn.execute("INSERT INTO staff_assignments (org_id, entity_id, assigned_to_user_id) "
                               "VALUES ($1, $2, $3)", ORG_A, eid, U_STAFF)
        # enum labels read from the catalog (kyc_status / ofac_status); pep_status is boolean
        await conn.execute("INSERT INTO compliance_records (org_id, entity_id, kyc_status, ofac_screen_status, "
                           "pep_status) VALUES ($1, $2, 'approved'::kyc_status, 'passed'::ofac_status, true)",
                           ORG_A, E_F2)
        await conn.execute("INSERT INTO entity_notes (org_id, entity_id, note_text) VALUES ($1, $2, $3)",
                           ORG_A, E_F2, f"{F2_NAME} private note")
        # fix 1: DOC_F2 carries a stored file (storage_key) so the download route has something to sign; DOC_M1
        # has none (storage_key NULL) — the "no stored file" 404 case
        await conn.execute("INSERT INTO entity_documents (id, org_id, entity_id, title, storage_key) VALUES "
                           "($1, $2, $3, $4, $7), ($5, $2, $6, 'BF2 member doc', NULL)", DOC_F2, ORG_A, E_F2,
                           f"{F2_NAME} doc", DOC_M1, E_M1, DOC_F2_KEY)
        await conn.execute("INSERT INTO entity_groups (id, org_id, name) VALUES ($1, $2, 'BF2 group')", G_F2, ORG_A)
        await conn.execute("INSERT INTO entity_group_members (org_id, group_id, entity_id) VALUES ($1, $2, $3)",
                           ORG_A, G_F2, E_F2)

        for did, st, nm in ((D_DRAFT, "draft", "draft"), (D_REVIEW, "under_review", "review"),
                            (D_SUBMITTED, "submitted", "submitted"), (D_ACTIVE, "active", "active")):
            await conn.execute(
                "INSERT INTO deals (id, org_id, name, slug, deal_status, description, asset_super_class, target_raise) "
                "VALUES ($1, $2, $3, $4, $5::deal_status, 'd', 'taxonomy_sc_1', 1000000)",
                did, ORG_A, f"BF2 Deal {nm}", f"{TAG}-{nm}", st)
        await conn.execute("INSERT INTO deals (id, org_id, name, slug, deal_status) VALUES ($1, $2, 'BF2 Org B Deal', "
                           "$3, 'active')", D_B, ORG_B, f"{TAG}-b")
        await conn.execute("INSERT INTO spvs (id, org_id, deal_id, name) VALUES ($1, $2, $3, 'BF2 vehicle')",
                           V_SPV, ORG_A, D_ACTIVE)

        # profiles / permission sets: one STAFF-ONLY (keys outside the member role), one member-safe
        for pid, name, keys in ((P_STAFFONLY, "BF2 Staff profile", ("staff", "view_workflow_runs", "manage_documents")),
                                (P_SAFE, "BF2 Member profile", ("view_dashboard", "view_deals"))):
            await conn.execute("INSERT INTO profiles (id, org_id, name) VALUES ($1, $2, $3)", pid, ORG_A, name)
            for k in keys:
                await conn.execute("INSERT INTO profile_permissions (org_id, profile_id, permission_key) "
                                   "VALUES ($1, $2, $3)", ORG_A, pid, k)
        await conn.execute("INSERT INTO permission_sets (id, org_id, name) VALUES ($1, $2, 'BF2 Staff set')",
                           PS_STAFFONLY, ORG_A)
        for k in ("view_members", "view_workflow_runs"):
            await conn.execute("INSERT INTO permission_set_permissions (org_id, permission_set_id, permission_key) "
                               "VALUES ($1, $2, $3)", ORG_A, PS_STAFFONLY, k)
        # member 2 ALREADY holds a staff-only profile + set (the jpl991721 shape), written directly
        await conn.execute("UPDATE users SET profile_id = $1 WHERE id = $2", P_STAFFONLY, U_MEMBER2)
        await conn.execute("INSERT INTO user_permission_sets (user_id, permission_set_id) VALUES ($1, $2)",
                           U_MEMBER2, PS_STAFFONLY)

        # workflows: one never run, one with a run
        bpmn = ('<?xml version="1.0" encoding="UTF-8"?><definitions xmlns="http://www.omg.org/spec/BPMN/20100524/MODEL" '
                'id="d"><process id="p" isExecutable="true"><startEvent id="s"/><userTask id="u" name="Approve"/>'
                '<endEvent id="e"/><sequenceFlow id="f1" sourceRef="s" targetRef="u"/>'
                '<sequenceFlow id="f2" sourceRef="u" targetRef="e"/></process></definitions>')
        for wid, vid, name in ((WF_NORUN, WFV_NORUN, "BF2 never run"), (WF_RUN, WFV_RUN, "BF2 has a run")):
            await conn.execute("INSERT INTO workflow_definitions (id, org_id, name) VALUES ($1, $2, $3)", wid, ORG_A,
                               name)
            # fix 1 (FAIL D): is_current = true, as services.workflow_nl_generator stores a generated version
            # (the column DEFAULTs to false; GET /admin/workflows/{id} reads the current version only)
            await conn.execute("INSERT INTO workflow_versions (id, workflow_definition_id, org_id, version_number, "
                               "bpmn_xml, is_current) VALUES ($1, $2, $3, 1, $4, true)", vid, wid, ORG_A, bpmn)
        await conn.execute("INSERT INTO workflow_runs (id, workflow_version_id, org_id) VALUES ($1, $2, $3)",
                           WFR_RUN, WFV_RUN, ORG_A)
    # VERIFYFIX1 (bugfix4: public.user_roles is the ONE role source; users.role only mirrors it) — assert the APP
    # decides every fixture user as the seed means (grants exactly, Super Admin / staff / permissions).
    await VR.check_roles_as_meant(conn, check, {U_SUPER: (ORG_A, {"super_admin"}), U_STAFF: (ORG_A, {"investment_staff"}), U_ORGADMIN: (ORG_A, {"org_admin"}), U_MEMBER: (ORG_A, {"member"}), U_MEMBER2: (ORG_A, {"member"}), U_OTHER: (ORG_B, {"member"})})


async def y_fixtures_are_test(conn):
    section("[Y] Fixture orgs and users are is_test = true")
    o = await platform(conn, "SELECT count(*) FROM organizations WHERE id = ANY($1::uuid[]) AND is_test", FIXTURE_ORGS,
                       fetch="fetchval")
    u = await platform(conn, "SELECT count(*) FROM users WHERE id = ANY($1::uuid[]) AND is_test", list(USERS),
                       fetch="fetchval")
    check(o == 2 and u == len(USERS), "both fixture orgs and every fixture user are is_test = true", f"{o} / {u}")


# ═══ The real app, token stubbed by bearer string ═════════════════════════════════════════════════════════
CLAIMS_BY_TOKEN: dict[str, dict] = {}
NETWORK_CALLS: list = []


def claims(uid: UUID) -> dict:
    return {"sub": sub_of(uid), "org_id": str(USERS[uid][0])}


def token_for(c: dict) -> str:
    key = "t" + str(abs(hash(json.dumps(c, sort_keys=True))))
    CLAIMS_BY_TOKEN[key] = dict(c)
    return key


async def api(uid, method, path, body=None, timeout=90.0):
    import main

    headers = {"Authorization": f"Bearer {token_for(claims(uid))}"}
    kw = {"headers": headers}
    if body is not None:
        kw["json"] = body
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app, raise_app_exceptions=False),
                                 base_url="http://verify", timeout=timeout) as client:
        try:
            res = await asyncio.wait_for(client.request(method, path, **kw), timeout)
        except asyncio.TimeoutError:
            return 599, {"timeout": True}
    try:
        parsed = json.loads(res.text)
    except ValueError:
        parsed = {"raw": res.text[:300]}
    return res.status_code, parsed


def install_stubs():
    import main
    from services import document_embedding, storage
    from services import users as users_mod
    import routers.org_settings as org_settings_router

    main.verify_token = lambda tok: dict(CLAIMS_BY_TOKEN[tok])
    from services.brief_blocks import register_brief_blocks

    register_brief_blocks()   # ASGITransport skips the lifespan that registers the brief blocks

    async def no_profile(request, c):
        return None

    users_mod.fetch_auth0_profile = no_profile

    async def no_live(rows):           # GET /admin/model-catalog enriches from the LiteLLM proxy — no network
        NETWORK_CALLS.append("litellm")
        return rows

    org_settings_router.enrich_with_live_info = no_live

    async def no_semantic(pool, org_id, user_id, query, *, is_staff=True, limit=20):
        return []

    document_embedding.semantic_search = no_semantic

    def fake_url(*a, **k):
        return "https://r2.invalid/bugfix2-verify"

    def no_r2(*a, **k):
        raise AssertionError("verify: real R2 must never be called")

    storage.get_signed_url = fake_url
    import routers.entity_documents as entity_documents_router   # fix 1: it imports get_signed_url by name

    entity_documents_router.get_signed_url = fake_url
    storage.get_s3_client = no_r2
    storage.upload_bytes = no_r2
    storage.delete_object = no_r2


def leaks_family2(body) -> bool:
    text = json.dumps(body, ensure_ascii=False, default=str)
    return any(s in text for s in (str(E_F2), str(E_F2T), str(E_F2L), F2_NAME, str(DOC_F2)))


# ═══ Sections ═════════════════════════════════════════════════════════════════════════════════════════════
async def y_role_is_real(conn):
    section("[Y] The verify connection is a genuinely non-bypassing role")
    r = await conn.fetchrow("SELECT current_user AS u, rolbypassrls FROM pg_roles WHERE rolname = current_user")
    check(r["rolbypassrls"] is False, "the app connection's role has rolbypassrls = false (RLS is real)",
          f"{r['u']} rolbypassrls={r['rolbypassrls']}")


def _mount_before_entities():
    """The PRE-FIX routers/entities.py (BEFORE_REF) as its own module, mounted under /__bf2before behind the SAME
    middleware / Principal — the exact original handlers on the identical request. Returns the prefix."""
    import main

    src = VS.git_show(BEFORE_REF, "apps/api/routers/entities.py")
    if not src:
        raise RuntimeError("cannot read routers/entities.py at BEFORE_REF")
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="bf2before-")) / "entities_before_bugfix2.py"
    tmp.write_text(src, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("entities_before_bugfix2", tmp)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    main.app.include_router(mod.router, prefix="/__bf2before/api/v1")
    return "/__bf2before"


async def y_critical(conn):
    section("[Y] The Critical — a member reads ANOTHER family's CRM record: reproduced BEFORE, gone AFTER")
    prefix = _mount_before_entities()
    paths = [f"/api/v1/entities/{E_F2}", f"/api/v1/entities/{E_F2}/full", f"/api/v1/entities/{E_F2}/notes",
             f"/api/v1/entities/{E_F2}/ownership-graph"]
    for p in paths:
        code, body = await api(U_MEMBER, "GET", prefix + p)
        check(code == 200 and leaks_family2(body), f"BEFORE (BEFORE_REF handler): member GET {p} → 200 with family "
              "2's data", f"HTTP {code}; {dumps(body)[:160]}")
    code, body = await api(U_MEMBER, "GET", f"{prefix}/api/v1/entities/{E_F2}/full")
    comp = (body or {}).get("compliance_record") or {}
    check(code == 200 and comp.get("kyc_status") == "approved" and comp.get("ofac_screen_status") == "passed"
          and comp.get("pep_status") is True,
          "BEFORE: the full record carried family 2's KYC / OFAC / PEP compliance record to a member",
          f"{dumps(comp)}")
    for p in paths:
        code, body = await api(U_MEMBER, "GET", p)
        check(code in (403, 404) and not leaks_family2(body), f"AFTER: member GET {p} → 403/404 with no data",
              f"HTTP {code}; {dumps(body)}")
        code_s, body_s = await api(U_STAFF, "GET", p)
        check(code_s == 200 and leaks_family2(body_s), f"AFTER: staff GET {p} → 200 (same request, admitted)",
              f"HTTP {code_s}; {dumps(body_s)[:200]}")
    code, body = await api(U_MEMBER, "GET", f"/api/v1/entities/{E_F2}/compliance")
    check(code == 403 and "view_compliance" in dumps(body), "AFTER: member GET /entities/{id}/compliance → 403",
          f"HTTP {code}")
    code, body = await api(U_STAFF, "GET", f"/api/v1/entities/{E_F2}/full")
    check(code == 200 and (body or {}).get("compliance_record", {}) is not None,
          "staff holding view_compliance still sees the compliance record in /full", f"HTTP {code}")


def _fill(path: str) -> tuple[str, str]:
    """A concrete request for a reclassified route: (path?query, what a member must get)."""
    p = path
    p = p.replace("{entity_id}", str(E_F2)).replace("{deal_id}", str(D_DRAFT)).replace("{group_id}", str(G_F2))
    p = p.replace("{record_type}", "entity").replace("{record_id}", str(E_F2)).replace("{doc_id}", str(DOC_F2))
    p = re.sub(r"\{[^}]+\}", str(MISSING), p)
    q = {
        "/api/v1/portfolio/targets": f"?entity_id={E_F2}",
        "/api/v1/portfolio/allocations": f"?entity_id={E_F2}",
        "/api/v1/allocation-lens": f"?selector_type=entity&entity_id={E_F2}",
        "/api/v1/modeling/ta/calibration-floor": "?periods_per_year=4",
        "/api/v1/document-search": "?q=BF2",
        "/api/v1/semantic-search": "?q=BF2",
        "/api/v1/entities/search": "?q=BF2",
        **{r: f"?vehicle_id={V_SPV}" for r in LEDGER_VEHICLE_ROUTES},   # fix 1 (FAIL B): a valid fixture vehicle
    }.get(path, "")
    return p + q


# fix 1: routes whose fixture rows are now seeded — staff must get exactly 200 on them (a 404 / 422 is not admitted)
MUST_BE_200 = set(LEDGER_VEHICLE_ROUTES) | {"/api/v1/entities/{entity_id}/documents/{doc_id}/download",
                                            "/api/v1/entity-groups/{group_id}"}


async def y_fix1(conn):
    section("[Y] fix 1 — ledger gate before validation; download / entity-group 500s; fixtures seeded")
    for r in LEDGER_VEHICLE_ROUTES:
        cm, bm = await api(U_MEMBER, "GET", r)                     # NO vehicle_id
        check(cm == 403 and "vehicle_id" not in dumps(bm), f"member GET {r} without vehicle_id → 403 (the gate runs "
              "before parameter validation; nothing about the route's parameters is disclosed)",
              f"HTTP {cm}; {dumps(bm)[:120]}")
        cm, bm = await api(U_MEMBER, "GET", f"{r}?vehicle_id={V_SPV}")
        cs, bs = await api(U_STAFF, "GET", f"{r}?vehicle_id={V_SPV}")
        check(cm == 403 and cs == 200, f"{r}?vehicle_id=<fixture vehicle>: member 403, staff 200",
              f"{cm}/{cs}; {dumps(bs)[:120]}")
        cs, bs = await api(U_STAFF, "GET", r)
        check(cs == 422, f"staff GET {r} without vehicle_id → 422 (validation still applies once admitted)", f"{cs}")
    dl = f"/api/v1/entities/{E_F2}/documents/{DOC_F2}/download"
    cs, bs = await api(U_STAFF, "GET", dl)
    check(cs == 200 and (bs or {}).get("url") == "https://r2.invalid/bugfix2-verify",
          "staff download of a stored document → 200 with the (stubbed) signed URL", f"HTTP {cs}; {dumps(bs)}")
    cs, bs = await api(U_STAFF, "GET", f"/api/v1/entities/{E_M1}/documents/{DOC_M1}/download")
    check(cs == 404 and "no stored file" in dumps(bs), "download of a record with no stored file → 404, never 500",
          f"HTTP {cs}; {dumps(bs)}")
    cs, bs = await api(U_STAFF, "GET", f"/api/v1/entities/{E_F2}/documents/{MISSING}/download")
    check(cs == 404, "download of a missing document → 404", f"HTTP {cs}")
    cm, bm = await api(U_MEMBER, "GET", dl)
    check(cm == 404 and not leaks_family2(bm), "member download of another family's document → 404, no data",
          f"HTTP {cm}")
    cs, bs = await api(U_STAFF, "GET", f"/api/v1/entity-groups/{G_F2}")
    mem = [m.get("entity_id") for m in (bs or {}).get("members") or []]
    check(cs == 200 and (bs or {}).get("id") == str(G_F2) and mem == [str(E_F2)]
          and all(m.get("added_at") for m in bs["members"]),
          "staff GET /entity-groups/{id} → 200 with its seeded member (was 500: updated_at / added_at columns)",
          f"HTTP {cs}; {dumps(bs)[:160]}")
    db = [str(x["entity_id"]) for x in await scoped(conn, ORG_A, "SELECT entity_id FROM entity_group_members "
                                                    "WHERE group_id = $1", G_F2)]
    check(mem == db, "…members equal the same rows read directly in SQL", f"{mem} vs {db}")
    cs, _ = await api(U_STAFF, "GET", f"/api/v1/entity-groups/{MISSING}")
    check(cs == 404, "staff GET /entity-groups/<missing> → 404 (was 500)", f"HTTP {cs}")
    cm, bm = await api(U_MEMBER, "GET", f"/api/v1/entity-groups/{G_F2}")
    check(cm == 403 and not leaks_family2(bm), "member GET /entity-groups/{id} → 403, no data", f"HTTP {cm}")


async def y_reclassified(conn):
    section("[Y] EVERY reclassified route, both ways — identical request: a member refused / scoped, staff admitted")
    before = json.loads(VS.git_show(BEFORE_REF, "docs/AUTHZ1_ROUTES.json") or "{}")
    now = json.loads(ROUTES_JSON.read_text(encoding="utf-8"))
    b = _open_reads(before)
    after = {(tuple(r["methods"]), r["path"], r["name"]): r for r in now["routes"]}
    # VERIFYFIX1 (handed off by CATCHALL1 / verify_vhk2): the 45 are bugfix2's OWN reclassifications — counted at
    # bugfix2's own end ref (BEFORE_REF → end, both pinned history), never against TODAY's record, which any later
    # sprint that gates one more of those open routes would change. Today's rule is asserted per route instead:
    # each of the 45 still exists and is still gated.
    end_ref, how = VS.sprint_end_ref(SPRINT_KEY)
    at_end = {(tuple(r["methods"]), r["path"], r["name"]): r
              for r in json.loads(VS.git_show(end_ref, "docs/AUTHZ1_ROUTES.json") or "{}").get("routes", [])}
    own = [k for k in b if k in at_end and at_end[k]["after"] != "none beyond auth"]
    check(len(own) == 45, "45 reclassified read routes found in bugfix2's own record (BEFORE_REF → its end ref)",
          f"{len(own)} ({BEFORE_REF} → {how})")
    gone = [k for k in own if k not in after]
    reopened = [k for k in own if k in after and after[k]["after"] == "none beyond auth"]
    check(not gone and not reopened, "each of bugfix2's reclassified routes still exists today and is still gated",
          f"gone {gone[:4]}; reopened {reopened[:4]}")
    changed = [(k, after[k]) for k in own if k in after]
    bad_m, bad_s, bad_o = [], [], []
    for k, r in changed:
        path = _fill(r["path"])
        rule = r["expect"]["rule"]
        cm, bm = await api(U_MEMBER, "GET", path)
        cs, bs = await api(U_STAFF, "GET", path)
        # STAFFROLES1: the staff-only reads now carry "internal" / "internal_perm" (any internal firm role sees
        # staff data; a member is still refused 403) — the same member expectation as "staff".
        if rule in ("staff", "internal", "internal_perm") or r["path"] in STAFF_OR_ORGADMIN:
            if not (cm == 403 and not leaks_family2(bm)):
                bad_m.append(f"{r['path']} → {cm} {dumps(bm)[:80]}")
        else:  # member-scoped: another family's id → 404; a list → 200 without family 2
            if leaks_family2(bm) or cm in (401, 500) or (cm == 403 and "Staff access" in dumps(bm)):
                bad_m.append(f"{r['path']} → {cm} {dumps(bm)[:80]}")
        if cs in (401, 403) or cs >= 500 or (r["path"] in MUST_BE_200 and cs != 200):
            bad_s.append(f"{r['path']} → {cs} {dumps(bs)[:80]}")
        if r["path"] in STAFF_OR_ORGADMIN:
            co, bo = await api(U_ORGADMIN, "GET", path)
            if co in (401, 403) or co >= 500:
                bad_o.append(f"{r['path']} → {co} {dumps(bo)[:80]}")
    check(not bad_m, "every reclassified route: the fixture MEMBER is refused (403, no data) or scoped (404 / no "
          "family-2 data)", f"{len(bad_m)}: {bad_m[:6]}")
    check(not bad_s, "every reclassified route: the fixture STAFF user is admitted on the identical request",
          f"{len(bad_s)}: {bad_s[:6]}")
    check(not bad_o, "settings catalogs (staff or manage_org_settings): the org admin is admitted", f"{bad_o}")
    check(NETWORK_CALLS.count("litellm") >= 1, "the model catalog's live-info enrichment hit the local stub, "
          "never the network", f"{NETWORK_CALLS}")


async def y_member_scope(conn):
    section("[Y] Member scope — own family only on every member-scoped route; another family → 404")
    from services.document_embedding import visible_entity_ids
    from services.database import get_pool

    pool = await get_pool()
    from services.database import reset_rls_context, set_rls_context

    tok = set_rls_context(str(ORG_A), False)
    try:
        own = await visible_entity_ids(pool, str(ORG_A), str(U_MEMBER), False)
    finally:
        reset_rls_context(tok)
    check(str(E_M1) in own and str(E_F2) not in own and str(E_F2L) not in own,
          "the member visibility engine (delegate grant → subtree) gives the member family 1 only", f"{sorted(own)}")
    code, rows = await api(U_MEMBER, "GET", "/api/v1/entities?limit=500")
    ids = {r["id"] for r in rows} if isinstance(rows, list) else set()
    check(code == 200 and bool(ids) and ids <= own and str(E_F2) not in ids,
          "GET /entities (Investment Profile's dropdown): non-empty, a subset of the member's own family, no family 2",
          f"HTTP {code}; {sorted(ids)}")
    sql_ids = {str(r["id"]) for r in await scoped(
        conn, ORG_A, "SELECT id FROM entities WHERE org_id = $1 AND id = ANY($2::uuid[]) AND valid_to IS NULL "
                     "AND system_to IS NULL AND is_active", ORG_A, [UUID(x) for x in own])}
    check(ids == sql_ids, "…and equals the same predicate run directly in SQL", f"{sorted(ids)} vs {sorted(sql_ids)}")
    code_s, rows_s = await api(U_STAFF, "GET", "/api/v1/entities?limit=500")
    sids = {r["id"] for r in rows_s} if isinstance(rows_s, list) else set()
    check(code_s == 200 and {str(E_M1), str(E_F2), str(E_F2L)} <= sids and ids < sids,
          "staff list is unscoped (both families) — the member's is a strict subset", f"{len(sids)}")
    code, s = await api(U_MEMBER, "GET", "/api/v1/entities/search?q=BF2")
    items = {i["id"] for i in (s or {}).get("items", [])}
    check(code == 200 and bool(items) and items <= own, "GET /entities/search is member-scoped", f"{sorted(items)}")
    for p, want in ((f"/api/v1/entities/{E_M1}", 200), (f"/api/v1/entities/{E_F2}", 404),
                    (f"/api/v1/entities/{E_M1}/documents", 200), (f"/api/v1/entities/{E_F2}/documents", 404),
                    (f"/api/v1/portfolio/targets?entity_id={E_M1}", 200),
                    (f"/api/v1/portfolio/targets?entity_id={E_F2}", 404),
                    (f"/api/v1/allocation-lens?selector_type=entity&entity_id={E_F2}", 404)):
        code, body = await api(U_MEMBER, "GET", p)
        check(code == want and (want == 200 or not leaks_family2(body)), f"member GET {p} → {want}",
              f"HTTP {code}; {dumps(body)[:120]}")


async def y_billing(conn):
    section("[Y] Billing groups / custody import reads — view_portfolio is a member grant; now staff only too")
    for p in ("/api/v1/billing-groups", "/api/v1/custody/batches", "/api/v1/custody/profiles"):
        cm, bm = await api(U_MEMBER, "GET", p)
        cs, bs = await api(U_STAFF, "GET", p)
        check(cm == 403 and "Staff access required" in dumps(bm) and cs not in (401, 403) and cs < 500,
              f"GET {p}: member (holds view_portfolio) 403, staff admitted — identical request",
              f"member {cm} {dumps(bm)[:80]}; staff {cs} {dumps(bs)[:80]}")


async def y_menu(conn):
    section("[Y] Menu and direct URLs — node tests over the real /users/me personas")
    code, out, err = run_cmd(["node", "--test", "tests/bugfix2/member_isolation.test.mjs", "tests/nav/"], WEB_DIR,
                             timeout=600)
    m = re.search(r"^# fail (\d+)", out, re.M)
    p = re.search(r"^# pass (\d+)", out, re.M)
    check(code == 0 and m is not None and m.group(1) == "0" and p is not None and int(p.group(1)) >= 7,
          "member menu has no staff item; staff menu unchanged; area gates refuse a member (CRM, SPV, admin, tilts) "
          "and fail closed", (out + err)[-400:] if code else f"{p.group(0) if p else ''}")
    code, me = await api(U_MEMBER, "GET", "/api/v1/users/me")
    code2, me2 = await api(U_STAFF, "GET", "/api/v1/users/me")
    check(code == 200 and me.get("is_staff") is False and code2 == 200 and me2.get("is_staff") is True,
          "/users/me publishes the DB staff flag the menu gates read", f"{me.get('is_staff')} / {me2.get('is_staff')}")


async def y_deals(conn):
    section("[Y] Deals — draft / submitted / under-review never reach a member; published (active) does")
    hidden = {str(D_DRAFT), str(D_REVIEW), str(D_SUBMITTED)}
    code, rows = await api(U_MEMBER, "GET", "/api/v1/deals?limit=200")
    ids = {r["id"] for r in rows} if isinstance(rows, list) else set()
    check(code == 200 and str(D_ACTIVE) in ids and not (ids & hidden),
          "GET /deals (member): the active deal, none of draft / submitted / under_review", f"{sorted(ids)}")
    for st in ("draft", "under_review", "submitted"):
        code, rows = await api(U_MEMBER, "GET", f"/api/v1/deals?status={st}")
        check(code == 200 and rows == [], f"GET /deals?status={st} (member) → [] (the filter narrows, never widens)",
              f"{dumps(rows)}")
    code, rows = await api(U_STAFF, "GET", "/api/v1/deals?limit=200")
    sids = {r["id"] for r in rows} if isinstance(rows, list) else set()
    check(code == 200 and hidden <= sids, "staff still list every status", f"{len(sids)}")
    for did in (D_DRAFT, D_REVIEW):
        for suffix in ("", "/taxonomy-placement", "/lifecycle", "/ai-summary", "/classes"):
            code, body = await api(U_MEMBER, "GET", f"/api/v1/deals/{did}{suffix}")
            check(code == 404 and "Deal not found" in dumps(body), f"member GET /deals/{{non-published}}{suffix} → "
                  "404 Deal not found", f"HTTP {code}; {dumps(body)[:100]}")
    code, _ = await api(U_MEMBER, "GET", f"/api/v1/deals/{D_ACTIVE}")
    code_s, _ = await api(U_STAFF, "GET", f"/api/v1/deals/{D_DRAFT}")
    check(code == 200 and code_s == 200, "member reads the active deal; staff reads the draft", f"{code} / {code_s}")
    code, brief = await api(U_MEMBER, "GET", "/api/v1/dashboard/brief")
    blocks = (brief or {}).get("blocks") or []
    nd = next((b for b in blocks if b.get("key") == "new_deals"), None)
    items = {str(i.get("id")) for i in ((nd or {}).get("data") or {}).get("items", [])}
    check(code == 200 and nd is not None and str(D_ACTIVE) in items and not (items & hidden),
          "dashboard NEW DEALS (member): the active deal, no draft / submitted / under_review", f"{sorted(items)}")
    from services.deal_lifecycle import MEMBER_VISIBLE_STATUSES

    check(tuple(MEMBER_VISIBLE_STATUSES) == ("active",), "the ONE member-visible set is ('active',)",
          f"{MEMBER_VISIBLE_STATUSES}")


async def y_permission_sets(conn):
    section("[Y] Staff-only profiles / permission sets — refused for a member; a member holding one gains nothing")
    from services import profiles as P
    from services.database import get_pool

    code, body = await api(U_ORGADMIN, "PUT", f"/api/v1/admin/users/{U_MEMBER}/profile",
                           {"profile_id": str(P_STAFFONLY)})
    pid = await platform(conn, "SELECT profile_id FROM users WHERE id = $1", U_MEMBER, fetch="fetchval")
    check(code == 409 and "staff" in dumps(body) and pid is None,
          "assigning a staff-only PROFILE to a member → 409 naming the keys; users.profile_id unchanged (re-read)",
          f"HTTP {code}; {dumps(body)}; profile_id={pid}")
    code, body = await api(U_ORGADMIN, "POST", f"/api/v1/admin/permission-sets/{PS_STAFFONLY}/users",
                           {"user_id": str(U_MEMBER)})
    n = await platform(conn, "SELECT count(*) FROM user_permission_sets WHERE user_id = $1", U_MEMBER, fetch="fetchval")
    check(code == 409 and "view_members" in dumps(body) and n == 0,
          "assigning a staff-only PERMISSION SET to a member → 409; no row written (re-read)",
          f"HTTP {code}; {dumps(body)}; rows={n}")
    code, _ = await api(U_ORGADMIN, "PUT", f"/api/v1/admin/users/{U_MEMBER}/profile", {"profile_id": str(P_SAFE)})
    pid = await platform(conn, "SELECT profile_id FROM users WHERE id = $1", U_MEMBER, fetch="fetchval")
    check(code == 200 and pid == P_SAFE, "a member-safe profile is still assignable to a member", f"HTTP {code}")
    code, _ = await api(U_ORGADMIN, "PUT", f"/api/v1/admin/users/{U_STAFF}/profile", {"profile_id": str(P_STAFFONLY)})
    check(code == 200, "the staff-only profile is still assignable to a STAFF user", f"HTTP {code}")
    await api(U_ORGADMIN, "PUT", f"/api/v1/admin/users/{U_STAFF}/profile", {"profile_id": None})

    pool = await get_pool()
    from services.database import reset_rls_context, set_rls_context

    tok = set_rls_context(str(ORG_A), False)
    try:
        raw = await P.get_profile_permissions(pool, P_STAFFONLY) | await P.get_permission_set_permissions(pool, U_MEMBER2)
        eff = await P.get_effective_permissions(pool, U_MEMBER2)
    finally:
        reset_rls_context(tok)
    check({"staff", "view_workflow_runs", "view_members"} <= raw,
          "BEFORE (the holding itself): member 2's profile + set carry staff, view_workflow_runs, view_members",
          f"{sorted(raw)}")
    check(not (eff & {"staff", "view_workflow_runs", "view_members", "manage_documents"}) and eff <= set(C.MEMBER_PERMS),
          "AFTER: member 2's effective profile-axis grants are member-safe only", f"{sorted(eff)}")
    for p in ("/api/v1/admin/workflow-runs", f"/api/v1/entities/{E_F2}/full", "/api/v1/ledger/accounts",
              f"/api/v1/entities/{E_F2}/notes"):
        code, body = await api(U_MEMBER2, "GET", p)
        check(code == 403 and not leaks_family2(body), f"member 2 (holding the staff-only profile + set) GET {p} → 403",
              f"HTTP {code}; {dumps(body)[:100]}")


async def y_operator_list(conn):
    section("[Y] Operator list — test-account corrections and every real member holding a staff-only set")
    from services.authz import is_member_principal, load_principal, staff_only_keys

    print("    [OPERATOR] test-account corrections (data — NOT changed by this sprint):")
    print("      jpl991723@gmail.com → org admin (Muse's 'org admin 2'; today role member)")
    print("      jpl991722@gmail.com → a staff role, e.g. investment_staff (Muse's 'support staff'; today member)")
    print("      jpl991721@gmail.com → remove permission set 'Compliance Override' AND profile 'CSA / Ops'")
    rows = await platform(conn, """
        SELECT u.id, u.email, u.org_id, p.name AS profile,
               ARRAY(SELECT pp.permission_key FROM profile_permissions pp WHERE pp.profile_id = u.profile_id) AS pkeys,
               ARRAY(SELECT ps.name FROM user_permission_sets ups JOIN permission_sets ps ON ps.id = ups.permission_set_id
                      WHERE ups.user_id = u.id) AS sets,
               ARRAY(SELECT psp.permission_key FROM user_permission_sets ups JOIN permission_set_permissions psp
                      ON psp.permission_set_id = ups.permission_set_id WHERE ups.user_id = u.id) AS skeys
        FROM users u LEFT JOIN profiles p ON p.id = u.profile_id
        WHERE NOT (u.org_id = ANY($1::uuid[])) AND u.status = 'active'
          AND (u.profile_id IS NOT NULL OR EXISTS (SELECT 1 FROM user_permission_sets x WHERE x.user_id = u.id))""",
                          FIXTURE_ORGS)
    holders = []
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        for r in rows:
            pr = await load_principal(conn, r["id"])
            if is_member_principal(pr):
                bad = await staff_only_keys(conn, r["org_id"], list(r["pkeys"] or []) + list(r["skeys"] or []))
                if bad:
                    holders.append((r["email"], r["profile"], list(r["sets"] or []), bad))
    for h in holders:
        print(f"      [OPERATOR] member {h[0]} holds staff-only keys {h[3]} via profile {h[1]!r} / sets {h[2]}")
    VS.when_empty(rows, "real users holding a profile or permission set are classified (member / not)",
                  lambda r: True, check=check, find=find)
    find("real MEMBERS holding a staff-only profile / set (grant nothing since BUGFIX2; listed for the operator)",
         f"{len(holders)}: {[h[0] for h in holders]}")


async def y_task5(conn):
    section("[Y] Task 5 — each HollisMuse High / Medium fix proven")
    from services import rbac

    # NAV-02 — reproduce: the old ensure_permission statement under an org admin's RLS context raises.
    old_sql = ("INSERT INTO permissions (name, resource, action) VALUES ($1, $2, $3) "
               "ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name RETURNING id")
    raised = None
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.current_org_id', $1, true), "
                           "set_config('app.is_super_admin', 'false', true)", str(ORG_A))
        try:
            async with conn.transaction():
                await conn.fetchval(old_sql, "manage_org_settings", "org", "manage")
        except Exception as exc:  # noqa: BLE001
            raised = type(exc).__name__
        pid = await rbac.ensure_permission(conn, "manage_org_settings", "org", "manage")
    check(raised is not None, "NAV-02 BEFORE: the old ensure_permission upsert raises under a non-super RLS context",
          f"{raised}")
    check(pid is not None, "NAV-02 AFTER: ensure_permission reads the existing row instead (no write)", f"{pid}")
    for p in ("/api/v1/admin/users", "/api/v1/admin/roles"):
        code, body = await api(U_ORGADMIN, "GET", p)
        check(code == 200, f"NAV-02: org admin GET {p} → 200 (was 500)", f"HTTP {code}; {dumps(body)[:120]}")

    # REP-05
    from services.models import unresolved_refusal
    from routers.models import resolution_links_for
    from services.authz import load_principal

    exc = unresolved_refusal([{"row_num": i, "raw_ticker": t} for i, t in ((2, "AAPL"), (3, "MSFT"), (4, "GOOG"))])
    d = exc.detail
    check(d["code"] == "unresolved_positions" and [r["identifier"] for r in d["unresolved"]] ==
          ["ticker AAPL", "ticker MSFT", "ticker GOOG"] and "To resolve" in d["message"] and len(d["steps"]) >= 3,
          "REP-05: the unresolved-rows refusal lists each row and says how to resolve it", f"{d['message'][:160]}")
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        p_staff = await load_principal(conn, U_STAFF)
        p_super = await load_principal(conn, U_SUPER)
    ls, lsa = resolution_links_for(p_staff, d["links"]), resolution_links_for(p_super, d["links"])
    check([l["href"] for l in ls] == ["/portfolio/securities"] and
          {l["href"] for l in lsa} == {"/portfolio/securities", "/admin/security-data"},
          "REP-05: the resolution links are only the screens the caller can open", f"{ls} / {lsa}")

    # REP-09
    from services import todo_links, workflow_todos

    h = todo_links.todo_href({"related_type": "workflow_run_step", "source": todo_links.HUMAN_TASK_TODO_SOURCE,
                              "action_key": "/portfolio/models/x"})
    h2 = todo_links.todo_href({"related_type": "workflow_run_step", "source": "workflow_user_task", "action_key": None})
    check(h == "/portfolio/models/x" and h2 == "/admin/workflows/runs"
          and todo_links.HUMAN_TASK_TODO_SOURCE == workflow_todos.TODO_SOURCE_USER_TASK,
          "REP-09: a human task's Open goes to the task's own subject page (run console only as fallback)",
          f"{h} / {h2}")

    # BT-01 / BT-02
    code, body = await api(U_SUPER, "POST", "/api/v1/replication/backtests", {"model_id": "abc", "fund_id": ""})
    check(code == 422 and isinstance(body.get("detail"), list), "BT-01: a bad backtest request answers with its "
          "reason (422 detail), not a bare failure", f"HTTP {code}; {dumps(body)[:120]}")
    from routers.replication_rep10 import backtest_failure_detail
    from services.hypothetical_policy import NO_POLICY

    msg = backtest_failure_detail(ValueError("prices missing for 3 days\nmore"))
    check("ValueError" in msg and "prices missing for 3 days" in msg and "more" not in msg,
          "BT-01: an unmapped failure's 500 detail names its type and reason", msg)
    check(NO_POLICY[0].isupper(), "BT-02: the no-policy sentence starts with a capital", NO_POLICY)

    # WF-01
    from services.bpmn_layout import ensure_bpmn_di, has_diagram

    xml = await platform(conn, "SELECT bpmn_xml FROM workflow_versions WHERE id = $1", WFV_NORUN, fetch="fetchval")
    drawn = ensure_bpmn_di(xml)
    check(not has_diagram(xml) and has_diagram(drawn) and ensure_bpmn_di(drawn) == drawn,
          "WF-01: a generator-style BPMN without a diagram gets one; a drawn one is unchanged")
    code, body = await api(U_SUPER, "GET", f"/api/v1/admin/workflows/{WF_NORUN}")
    shown = json.dumps(body)
    check(code == 200 and "BPMNDiagram" in shown, "WF-01: GET /admin/workflows/{id} returns a drawable diagram",
          f"HTTP {code}")

    # WF-03
    code, d = await api(U_STAFF, "GET", f"/api/v1/entities/{E_F2L}")
    owners = {o["parent_id"]: float(o["ownership_pct"]) for o in (d or {}).get("owners", [])}
    code2, t = await api(U_STAFF, "GET", f"/api/v1/entities/{E_F2L}/ownership-tree?direction=owned_by")
    kids = {c["id"]: float(c["ownership_pct"]) for c in ((t or {}).get("tree") or {}).get("children", [])}
    check(code == 200 and code2 == 200 and owners == {str(E_F2): 40.0, str(E_F2T): 60.0} and kids == owners,
          "WF-03: the staff tree (owned_by) shows the owners and percentages the detail page lists",
          f"detail {owners}; tree {kids}")
    code3, t3 = await api(U_STAFF, "GET", f"/api/v1/entities/{E_F2}/ownership-tree?direction=owns")
    kids3 = [c["id"] for c in ((t3 or {}).get("tree") or {}).get("children", [])]
    check(code3 == 200 and kids3 == [str(E_F2L)], "WF-03: the owns direction reads entity_ownership edges too",
          f"{kids3}")

    # TILT-02 (area gate) is proven by the node tests in y_menu; the API gate is unchanged:
    code, _ = await api(U_MEMBER, "GET", "/api/v1/replication/backtests")
    check(code == 403, "TILT-02: the replication API refuses a member (the layout now refuses the page too)",
          f"HTTP {code}")

    # Workflow delete
    code, body = await api(U_MEMBER, "DELETE", f"/api/v1/admin/workflows/{WF_NORUN}")
    check(code == 403, "workflow delete: a member → 403", f"HTTP {code}; {dumps(body)}")
    before = await platform(conn, "SELECT count(*) FROM workflow_versions WHERE workflow_definition_id = $1", WF_RUN,
                            fetch="fetchval")
    code, body = await api(U_SUPER, "DELETE", f"/api/v1/admin/workflows/{WF_RUN}")
    after_ = await platform(conn, "SELECT (SELECT count(*) FROM workflow_definitions WHERE id = $1) + "
                                  "(SELECT count(*) FROM workflow_versions WHERE workflow_definition_id = $1)",
                            WF_RUN, fetch="fetchval")
    check(code == 409 and before == 1 and after_ == 2, "workflow delete: a workflow WITH a run → 409, rows unchanged",
          f"HTTP {code}; {dumps(body)[:160]}")
    code, body = await api(U_SUPER, "DELETE", f"/api/v1/admin/workflows/{WF_NORUN}")
    gone = await platform(conn, "SELECT (SELECT count(*) FROM workflow_definitions WHERE id = $1) + "
                                "(SELECT count(*) FROM workflow_versions WHERE workflow_definition_id = $1)",
                          WF_NORUN, fetch="fetchval")
    check(code == 200 and gone == 0, "workflow delete: a never-run workflow → 200, definition + versions gone "
          "(independent re-read); no maker-checker for a never-run draft", f"HTTP {code}; left {gone}")

    # AI-08 + BT-01 client + REP-05 client: the node tests the sprint added / updated
    code, out, err = run_cmd(["node", "--test", "tests/replication/", "tests/models/", "tests/admin/"], WEB_DIR,
                             timeout=600)
    m = re.search(r"^# fail (\d+)", out, re.M)
    check(code == 0 and m is not None and m.group(1) == "0",
          "AI-08 / BT-01 / REP-05 client side: node tests pass (catalog loading state, errorFromDetail, "
          "unresolved help)", (out + err)[-300:] if code else "")
    catalog = (WEB_DIR / "lib" / "catalogLoadState.mjs").read_text(encoding="utf-8")
    check("showCount" in catalog, "AI-08: the catalog view state exists (no '0 models' while loading)")


async def y_muse(conn):
    section("[Y] The HollisMuse test rows are gone (removed by scripts/bugfix2_remove_muse_rows.py)")
    n = await platform(conn, "SELECT (SELECT count(*) FROM portfolio.models WHERE id = $1::uuid) + "
                             "(SELECT count(*) FROM portfolio.model_versions WHERE model_id = $1::uuid) + "
                             "(SELECT count(*) FROM workflow_definitions WHERE id = $2::uuid) + "
                             "(SELECT count(*) FROM workflow_versions WHERE workflow_definition_id = $2::uuid)",
                       MUSE_MODEL, MUSE_WORKFLOW, fetch="fetchval")
    check(n == 0, "zero MUSE model / version / workflow / workflow-version rows remain", f"{n}")


async def y_isolation(conn):
    section("[Y] Org isolation both directions (app_service, rolbypassrls = false)")
    r = await conn.fetchrow("SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user")
    check(r["rolbypassrls"] is False, "isolation is measured on a non-bypassing role")
    a = {x["id"] for x in await scoped(conn, ORG_A, "SELECT id FROM entities WHERE id = ANY($1::uuid[])", [E_M1, E_B])}
    b = {x["id"] for x in await scoped(conn, ORG_B, "SELECT id FROM entities WHERE id = ANY($1::uuid[])", [E_M1, E_B])}
    check(a == {E_M1} and b == {E_B}, "RLS: org A sees only its entity, org B only its own", f"{a} / {b}")
    code, _ = await api(U_OTHER, "GET", f"/api/v1/entities/{E_M1}")
    code2, _ = await api(U_STAFF, "GET", f"/api/v1/entities/{E_B}")
    code3, rows = await api(U_OTHER, "GET", "/api/v1/entities")
    ids = {x["id"] for x in rows} if isinstance(rows, list) else set()
    check(code == 404 and code2 == 404 and code3 == 200 and ids == {str(E_B)},
          "API: org B's member 404 on org A's entity; org A's staff 404 on org B's; org B lists only its own",
          f"{code}/{code2}/{code3} {ids}")


async def y_routes(conn):
    section("[Y] Routes — own changes via _verify_self; authz1_routes.py --check CURRENT")
    self_routes(check, find)
    code, out, err = run_cmd([sys.executable, str(HERE / "authz1_routes.py"), "--check"], REPO, timeout=600)
    check(code == 0 and "CURRENT" in out, "authz1_routes.py --check: the route record is CURRENT", (out + err)[-200:])


async def y_front_end(conn):
    section("[Y] Front end — node --test tests/; npm run build")
    code, out, err = run_cmd(["node", "--test", "tests/"], WEB_DIR, timeout=900)
    m = re.search(r"^# fail (\d+)", out, re.M)
    check(code == 0 and m is not None and m.group(1) == "0", "node --test tests/ passes (0 fail)",
          (out + err)[-300:] if code else "")
    code, out, err = run_cmd(["npm", "run", "build"], WEB_DIR, timeout=1800)
    check(code == 0, "npm run build exits 0", (out + err)[-400:] if code else "")


def regression():
    section("[Y] Regression — verify_authz1.py ONCE, flat, AFTER teardown, VERIFY_NESTED=1, 2700 s")
    env = os.environ | {"VERIFY_NESTED": "1"}
    code, out, err = run_cmd([sys.executable, str(HERE / "verify_authz1.py")], REPO, timeout=2700, env=env)
    nt = VT.nested_total(out, code)
    fails = [ln for ln in out.splitlines() if ln.startswith("[FAIL]")]
    check(nt.ok, "verify_authz1.py: 0 FAIL",
          f"{nt.detail()}; {fails[:4]} {err[-200:] if not nt.ok else ''}")


async def run(name, fn, *args):
    try:
        res = fn(*args)
        if asyncio.iscoroutine(res):
            await res
    except Exception as exc:  # noqa: BLE001
        check(False, f"{name} raised", f"{type(exc).__name__}: {exc}")
        traceback.print_exc()


async def main_async() -> int:
    dsn, how = await admin_dsn()
    if not dsn:
        print(f"[FAIL] no working DATABASE_URL: {how}")
        print("TOTAL: 0 PASS, 1 FAIL")
        return 1
    os.environ["DATABASE_URL"] = dsn
    print(f"[INFO] database via {how}; today {date.today().isoformat()}")
    conn = await connect(dsn)
    install_stubs()
    if "--cleanup-only" in sys.argv:
        return await cleanup_only(conn)
    pre = real = None
    real_cols = {}
    try:
        await run("pre-teardown", teardown, conn)
        pre = await counts(conn)
        real = {t: await fingerprint(conn, t) for t in REAL_FINGERPRINTED}
        real_cols = {t: await column_hashes(conn, t) for t in ACTIVITY_COLUMNS}
        await run("role", y_role_is_real, conn)
        await run("constraints", y_constraints, conn)
        await run("seed", seed, conn)
        await run("fixtures are test", y_fixtures_are_test, conn)
        section("[Y] Task 1 — findings 1a–1f (the 73-route table)")
        self_task1(check, find)
        await run("critical", y_critical, conn)
        await run("reclassified", y_reclassified, conn)
        await run("fix 1", y_fix1, conn)
        await run("member scope", y_member_scope, conn)
        await run("billing", y_billing, conn)
        await run("menu", y_menu, conn)
        await run("deals", y_deals, conn)
        await run("permission sets", y_permission_sets, conn)
        await run("operator list", y_operator_list, conn)
        await run("task 5", y_task5, conn)
        section("[Y] Task 6 + static")
        self_static(check, find)
        await run("muse", y_muse, conn)
        await run("isolation", y_isolation, conn)
        await run("routes", y_routes, conn)
        await run("front end", y_front_end, conn)
    finally:
        section("[Y] Teardown — zero fixture rows; every counted table at its pre-test count; real rows unchanged")
        try:
            from services.database import close_pool

            await close_pool()
        except Exception:  # noqa: BLE001
            pass
        try:
            log = await teardown(conn)
            print(f"[INFO] teardown removed: {log}")
            left = await leftovers(conn)
            check(left == 0, "zero fixture rows remain", f"{left}")
            if pre is not None:
                post = await counts(conn)
                for t in COUNTED:
                    check(post[t] == pre[t], f"{t} at its pre-test count", f"{pre[t]} → {post[t]}")
            if real is not None:
                for t in REAL_FINGERPRINTED:
                    now = await fingerprint(conn, t)
                    excl = ACTIVITY_COLUMNS.get(t, ())
                    check(now == real[t], f"real {t} unchanged (count + checksum by primary key"
                          + (f"; excluding activity column(s) {', '.join(excl)}" if excl else "") + ")",
                          f"{real[t][0]} → {now[0]} rows")
                for t, before_cols in real_cols.items():
                    diff = changed_columns(before_cols, await column_hashes(conn, t))
                    print(f"[INFO] real {t} rows changed during the run (id: column names; no values): "
                          f"{diff or 'none'}")
                    off = [d for d in diff if not set(d.split(': ', 1)[1].split(',')) <= set(ACTIVITY_COLUMNS[t])]
                    check(not off, f"real {t}: nothing but {', '.join(ACTIVITY_COLUMNS[t])} changed on any row "
                          "(no row added / removed; no role / status / org / profile change)", f"{off}")
        except Exception as exc:  # noqa: BLE001
            check(False, "teardown raised", f"{type(exc).__name__}: {exc}")
        finally:
            await conn.close()

    if os.environ.get("VERIFY_NESTED") == "1":
        print("[SKIP] nested run")
    else:
        await run("regression", regression)
    print(f"\nTOTAL: {_n_pass} PASS, {_n_fail} FAIL")
    return 1 if _n_fail else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main_async()))
