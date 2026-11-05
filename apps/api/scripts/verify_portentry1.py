"""verify_portentry1 — the Olivo entry-plan gaps crmdetail1 named, closed: CRM vocabularies (GAP-CRM-01…04), the
ownership gate and edit_crm_contact (GAP-CRM-05, 06), manual positions for any holding (GAP-PORT-01), the integrated
accounts' one-time import (GAP-PORT-02), direct assets and private investments (GAP-PORT-03, 04), net worth current
after writes (GAP-PORT-05), and _verify_self for fast-forwarded sprints.

Pass/fail only, no prompts. Prints 'TOTAL: N PASS, M FAIL' and exits non-zero on any failure. AUTHZ_TEST_MODE=1 is set
before anything imports ``main``; secrets are hydrated from Doppler over HTTPS (``_db_connect.hydrate``) before
``main`` is imported. No network: the Auth0 profile fetch and R2 are replaced by local stubs (each matches the CURRENT
signature it replaces). PORTFOLIO_ROLLUP_DEBOUNCE_S=0 so "net worth after a write" is deterministic (the family rollup
runs inline); the debounce itself is proven on the queue table.

Everything after the bearer token is the real app against the live database, in TWO fixture orgs
(99000000-…-000000540001 / 540002), ``is_test = true`` (asserted), as are the fixture users. One fixture security is
written to the platform master by THIS SCRIPT (never by the product) so a manual asset has something to relink to; the
master's count + checksum excluding it is asserted unchanged across every product action. Teardown at start and end:
the shared scoped cleanup (_verify_cleanup) on the operator DSN by fixture org / user ids, plus the fixture security by
id; every counted table is re-read for its exact pre-test count and the REAL rows are fingerprinted.

--self-checks-only: git / route-record / static checks only (no Doppler, DB, network or npm).
--cleanup-only: the teardown + leftover check only.
With VERIFY_NESTED=1 the regression section prints one '[SKIP] nested run'.
"""

from __future__ import annotations

import os as _authz_os
_authz_os.environ["AUTHZ_TEST_MODE"] = "1"  # AUTHZ1: set before main is imported
_authz_os.environ["PORTFOLIO_ROLLUP_DEBOUNCE_S"] = "0"   # the family rollup runs inline (deterministic)

import asyncio
import csv
import hashlib
import importlib.util
import io
import json
import os
import pathlib
import re
import subprocess
import sys
import traceback
from datetime import date
from decimal import Decimal
from uuid import UUID

HERE = pathlib.Path(__file__).resolve().parent
API_DIR = HERE.parent
REPO = API_DIR.parents[1]
WEB_DIR = REPO / "apps" / "web"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(API_DIR))

import _verify_cleanup as VC  # noqa: E402  (stdlib only)
import _verify_self as VS  # noqa: E402

SPRINT_KEY = "portentry1"
BEFORE_REF = "e5f5da8"   # portentry1-work base: main a0755ee + "chore: refresh schema snapshot"

IM = "internal + any(manage_members)"
NEW_ROUTES = {
    ("PUT", "/api/v1/entities/{entity_id}/contact"): "internal + any(manage_members|edit_crm_contact)",
    ("GET", "/api/v1/entities/{entity_id}/direct-assets"): "member scope",
    ("POST", "/api/v1/entities/{entity_id}/direct-assets"): IM,
    ("POST", "/api/v1/direct-assets/{asset_id}/valuations"): IM,
    ("GET", "/api/v1/entities/{entity_id}/private-investments"): "member scope",
    ("POST", "/api/v1/entities/{entity_id}/private-investments"): IM,
    ("POST", "/api/v1/private-investments/{commitment_id}/flows"): IM,
    ("POST", "/api/v1/private-investments/{commitment_id}/nav"): IM,
    ("GET", "/api/v1/households/{household_id}/assets"): "internal",
    ("POST", "/api/v1/entities/{entity_id}/net-worth/recalculate"): "internal + any(manage_members|manage_portfolio)",
    ("GET", "/api/v1/portfolio/manual-assets"): "authz view_held_away",
    ("POST", "/api/v1/portfolio/manual-assets/relink"): "authz manage_held_away",
    ("GET", "/api/v1/admin/integrated-imports"): "super_admin",
    ("POST", "/api/v1/admin/integrated-imports"): "super_admin",
    ("GET", "/api/v1/admin/integrated-imports/{import_id}"): "super_admin",
    ("POST", "/api/v1/admin/integrated-imports/{import_id}/approve"): "super_admin",
    ("POST", "/api/v1/admin/integrated-imports/{import_id}/reject"): "super_admin",
}
#: existing routes whose gate this sprint DELIBERATELY changed (Joe, 2026-11-04) → their new gate
CHANGED_ROUTES = {
    ("POST", "/api/v1/entities/{entity_id}/ownership"): IM,
    ("PATCH", "/api/v1/entity-relationships/{rel_id}/ownership"): IM,
    ("DELETE", "/api/v1/entity-relationships/{rel_id}/ownership"): IM,
    ("POST", "/api/v1/entity-ownership"): IM,
    ("POST", "/api/v1/entities/{entity_id}/notes"): "internal + any(manage_members|edit_crm_contact)",
    ("POST", "/api/v1/entities/{entity_id}/addresses"): "internal + any(manage_members|edit_crm_contact)",
    ("PUT", "/api/v1/entities/{entity_id}/addresses/{addr_id}"): "internal + any(manage_members|edit_crm_contact)",
}
INTEGRATED = ("A7", "A9", "A12", "A14", "A15", "A20", "A24", "A29")
GAPS = ("GAP-CRM-01", "GAP-CRM-02", "GAP-CRM-03", "GAP-CRM-04", "GAP-CRM-05", "GAP-CRM-06", "GAP-PORT-01",
        "GAP-PORT-02", "GAP-PORT-03", "GAP-PORT-04", "GAP-PORT-05")

# ── Task 1 — written by hand from the discovery; each needle must be present at the sprint's end. ───────────────────
TASK1 = [
    ("apps/api/routers/entities.py", "conditions.append(f\"status = ${len(params)}\")",
     "1a: the CRM list filters entities.status by exact match — adding client / contact breaks no reader"),
    ("apps/api/services/crm_config.py", "CAT_SUBTYPE = \"crm_sub_types\"",
     "1a: statuses / marital / sub-types now in public.config (were lib/entityTypes.js constants)"),
    ("apps/api/routers/entity_graph.py", "require_internal_permission(request, \"manage_members\")",
     "1b: the three ownership routes (POST /entities/{id}/ownership, PATCH / DELETE …/ownership) + POST "
     "/entity-ownership; verify_staffroles1 y_bypass asserted require_staff on DELETE …/ownership"),
    ("apps/api/services/held_away.py", "r[\"reject\"] = (\"no_identifier\", REJECT_REASONS[\"no_identifier\"])",
     "1c: held-away rows rejected at resolve_identifiers (no_identifier / unresolved); a manual asset is "
     "portfolio.assets with global_security_id NULL (zero existed)"),
    ("apps/api/services/portfolio_import.py", "IMPORT_AUTHORITY = \"aggregated\"",
     "1d: POST /portfolio/import/positions writes at once (no preview, no second person), authority aggregated, "
     "source reporting_tool_import — which ranks ABOVE altruist in the default precedence order"),
    ("apps/api/services/portfolio_commitments.py", "UNFUNDED_FORMULA = \"commitment_amount - called_to_date + "
     "recallable_amount\"", "1e: called / distributed / unfunded are DERIVED from transactions; TA reads the "
     "commitment and the position's market_value"),
    ("apps/api/services/portfolio_rollup.py", "scope: set[str] | None = None",
     "1f: the rollup was org-wide per date (deletes buckets it did not produce); now optionally family-scoped"),
    ("apps/api/scripts/_verify_self.py", "def _fast_forward(sprint_key: str)",
     "1g: no merge commit → fell through to HEAD; 37 regression verifies use sprint_end_ref"),
]
TASK1_FINDS = [
    "1a — entities.status is free text (no CHECK); readers: the CRM list's exact-match ?status= filter, the "
    "create default 'prospect', note-apply; MEMBER_VISIBLE_STATUSES is deals only. Live: 33 prospect, 3 active. No "
    "safe mapping of 'active' to client / contact exists (nothing on the row says which) — existing rows unchanged.",
    "1b — ownership writes: POST /entities/{id}/ownership, PATCH + DELETE /entity-relationships/{id}/ownership "
    "(require_staff) and POST /entity-ownership (manage_members alone). The hierarchy rail's generic POST / PATCH / "
    "DELETE /entity-relationships keeps require_staff (crmdetail1: gate unchanged) — stricter, not a hole.",
    "1c — rejects: no_identifier / unresolved (resolve_identifiers); _asset_for links an org asset to a master "
    "security; the assets of held-away lines carried NO taxonomy key, so the rollup skipped them.",
    "1d — manage_portfolio via rbac; per-row external-id idempotency; owner_entity_id per FILE; no account mask.",
    "1e — valuations append-only (forward supersedes pointer); TA falls back to called − distributed when the "
    "position has no market_value; 'call_investment' / 'dist_roc' are the opening-flow codes.",
    "1f — cost: one pass over the org's current positions + one look-through BFS per owning entity; trigger was "
    "only POST /portfolio/rollup. Net worth summed the latest bucket per taxonomy key — a key a later rollup "
    "dropped lingered from an earlier date (now: latest snapshot per entity + zero snapshots).",
    "1g — perms1 is a fast-forward; secmaster1's merge subject is git's 'Merge branch '…-work''; previewperf fix 1 "
    "and vhk2 keep their own pre-existing self-check FAILs (unrelated; unchanged by this sprint).",
]


def _read(rel: str) -> str:
    p = REPO / rel
    return p.read_text(encoding="utf-8") if p.exists() else ""


def _key(rec: dict) -> tuple:
    return ((rec.get("methods") or [""])[0], rec["path"])


def self_task1(check, find):
    for rel, needle, meaning in TASK1:
        txt = VS.own_text(SPRINT_KEY, rel) or ""
        check(needle in txt, f"Task 1 — {meaning}", f"{rel}: needle missing")
    for f in TASK1_FINDS:
        find(f"Task 1 — {f}")


def self_routes(check, find):
    own = VS.own_routes(BEFORE_REF, SPRINT_KEY)
    new = {_key(own.end[k]): own.end[k] for k in own.new}
    check(set(new) == set(NEW_ROUTES), f"the sprint added exactly its {len(NEW_ROUTES)} routes",
          f"{own.where()}; extra {sorted(set(new) - set(NEW_ROUTES))}; missing {sorted(set(NEW_ROUTES) - set(new))}")
    wrong = [(k, (new.get(k) or {}).get("after")) for k, want in NEW_ROUTES.items()
             if not str((new.get(k) or {}).get("after") or "").startswith(want)]
    check(not wrong, "every new route carries its stated gate", f"{wrong}")
    changed = {_key(own.end[k]): own.end[k].get("after") for k in own.changed}
    check(set(changed) == set(CHANGED_ROUTES) and all(changed[k] == v for k, v in CHANGED_ROUTES.items()),
          "exactly the decided gate changes (ownership: internal + manage_members; notes / addresses: + "
          "edit_crm_contact) — nothing else", f"{changed}")
    check(not own.removed, "no route removed", f"{own.removed}")
    drift = VS.still_gated(own)
    check(not drift, "the sprint's routes still carry the same gate today", f"{drift}")


def _load_verify_self_at(ref: str):
    src = VS.git_show(ref, "apps/api/scripts/_verify_self.py")
    if src is None:
        raise RuntimeError(f"no _verify_self.py at {ref}")
    spec = importlib.util.spec_from_loader(f"_vs_{ref}", loader=None)
    mod = importlib.util.module_from_spec(spec)
    mod.__dict__["__file__"] = str(HERE / "_verify_self.py")   # REPO / HERE resolve as in the real module
    exec(compile(src, f"_verify_self@{ref}", "exec"), mod.__dict__)   # noqa: S102 — our own file at a known ref
    return mod


def self_verify_self(check, find):
    """Task 8 — fast-forward detection; merged sprints unchanged; every other regression verify resolves its end
    exactly as before the change."""
    end, how = VS.sprint_end_ref("perms1")
    check(end == "3fe2cef" and VS.sprint_base_ref("perms1") == "01c0f57" and "fast-forward" in how,
          "perms1 (fast-forward, no merge commit) ends at its own tip 3fe2cef, base 01c0f57", f"{end}; {how}")
    ok, why = VS.own_base_ok("01c0f57", "perms1")
    check(ok, "perms1's BEFORE_REF 01c0f57 lies on its own line (the old FAIL: 'base … NOT an ancestor of 01c0f57')",
          why)
    ff = VS._fast_forward("perms1")
    check(ff == ("3fe2cef", "01c0f57"), "found from perms1's own first-parent commits, not only the stated table",
          f"{ff}")
    for key, merge, tip in (("secmaster1", "17ab37e", "1ded48b"), ("crmdetail1", "be26220", "53c6f40")):
        e, h = VS.sprint_end_ref(key)
        check(e == tip and VS.sprint_merge(key) == merge, f"{key} (merge commit {merge}) ends at its second parent "
              f"{tip}", f"{e}; {h}")
    old = _load_verify_self_at(BEFORE_REF)
    keys = sorted({m.group(1) for f in (HERE).glob("verify_*.py")
                   for m in [re.search(r'^SPRINT_KEY = "([^"]+)"', f.read_text(encoding="utf-8"), re.M)] if m})
    differ = []
    for k in keys:
        if k in ("perms1", "secmaster1", "crmdetail1", SPRINT_KEY):
            continue
        try:
            a = old.sprint_end_ref(k)[0]
        except Exception as exc:  # noqa: BLE001
            a = f"raised {type(exc).__name__}"
        try:
            b = VS.sprint_end_ref(k)[0]
        except Exception as exc:  # noqa: BLE001
            b = f"raised {type(exc).__name__}"
        if a != b and not (a == "HEAD" and b == "HEAD"):
            differ.append((k, a, b))
    check(bool(keys) and not differ, f"every other sprint key ({len(keys) - 3}) resolves its end exactly as the "
          f"pre-sprint _verify_self did", f"{differ}")
    for script in ("verify_perms1.py", "verify_secmaster1.py", "verify_crmdetail1.py"):
        code, out, err = run_cmd([sys.executable, str(HERE / script), VS.SELF_CHECKS_FLAG], REPO, timeout=600)
        m = re.search(r"TOTAL: (\d+) PASS, (\d+) FAIL", out)
        check(bool(code == 0 and m and m.group(2) == "0"), f"{script} --self-checks-only: 0 FAIL",
              f"{m.group(0) if m else 'no TOTAL'}; {[l for l in out.splitlines() if l.startswith('[FAIL]')][:3]}")
    rules = _read("docs/VERIFY_RULES.md")
    check(bool("--no-ff" in rules and "fast-forward" in rules.lower()), "docs/VERIFY_RULES.md: merges use --no-ff")


def self_static(check, find):
    sql = _read("docs/portentry1_part1.sql")
    check(bool("crm_seed_org_config" in sql and "edit_crm_contact" in sql and "portfolio.provisional_imports" in sql
               and "portfolio.rollup_requests" in sql and "BEGIN;" not in sql),
          "docs/portentry1_part1.sql saved (vocabularies, permission, provisional import, rollup queue)")
    check(bool("securities_global" not in re.sub(r"--.*", "", sql).replace("REFERENCES portfolio.securities_global",
                                                                           "")),
          "the migration writes nothing to the platform master")
    check(bool((HERE / "_apply_portentry1.py").exists()), "scripts/_apply_portentry1.py exists")
    check("verify_portentry1.py" in _read("scripts/regression_set.txt"), "Task 9: in scripts/regression_set.txt")
    status = _read("docs/PROJECT_STATUS.md")
    m_sec = re.search(r"\n## 0+\. PORTENTRY1 — .*?(?=\n## )", status, re.S)
    top = m_sec.group(0) if m_sec else ""
    check(bool("PORTENTRY1" in status[:3000] and all(g in top for g in GAPS) and "edit_crm_contact" in top
               and "integrated_positions.csv" in top), "Task 9: PROJECT_STATUS entry — every gap, the permission, the "
          "import screen, the operator steps")
    check(bool(re.search(r"UPDATE.*PORTENTRY1", status)), "Task 9: crmdetail1's named gaps carry UPDATE lines")
    authz = _read("apps/api/services/authz.py")
    m = re.search(r"STAFF_ROLE_NAMES = frozenset\(\{([^}]*)\}\)", authz)
    old = VS.git_show(BEFORE_REF, "apps/api/services/authz.py") or ""
    m0 = re.search(r"STAFF_ROLE_NAMES = frozenset\(\{([^}]*)\}\)", old)
    check(bool(m and m0 and m.group(1) == m0.group(1)), "the bypass set (STAFF_ROLE_NAMES) did not grow",
          f"{m.group(1) if m else None}")
    for rel in ("apps/web/components/crm/EntityDetailsForm.jsx", "apps/web/components/crm/NewEntityForm.jsx",
                "apps/web/app/crm/page.js"):
        t = _read(rel)
        check(bool("crmVocab" in t and "STATUS_OPTIONS" not in t), f"{rel}: vocabularies from the envelope")
    # the generated import file: eight accounts, masks only, totals equal holdings.csv
    data = REPO / "docs/demo/olivo/data"
    accts = {r["id"]: r for r in csv.DictReader((data / "accounts.csv").open(encoding="utf-8"))}
    want: dict[str, Decimal] = {}
    for h in csv.DictReader((data / "holdings.csv").open(encoding="utf-8")):
        if h["account_id"] in INTEGRATED:
            m_ = accts[h["account_id"]]["account_mask"]
            want[m_] = want.get(m_, Decimal(0)) + Decimal(h["value_usd"])
    got: dict[str, Decimal] = {}
    out = list(csv.DictReader((data / "import/integrated_positions.csv").open(encoding="utf-8")))
    for r in out:
        got[r["account_mask"]] = got.get(r["account_mask"], Decimal(0)) + Decimal(r["market_value"])
    check(bool(len(got) == 8 and got == want), "integrated_positions.csv: the eight integrated accounts, totals "
          "equal holdings.csv per account", f"{sorted(got.items())} vs {sorted(want.items())}")
    check(bool(out) and all(re.fullmatch(r"\*+\d{1,4}", r["account_mask"]) for r in out),
          "every account is identified by its MASK only (never a full number)")
    readme = _read("docs/demo/olivo/README.md")
    check(bool("integrated_positions.csv" in readme and "account_mask" in readme and "Protection rule (crmdetail1)"
               in readme), "Olivo README documents the CSV format; the demo protection rule is kept")


def self_checks(check, find):
    self_task1(check, find)
    self_verify_self(check, find)
    self_routes(check, find)
    self_static(check, find)


def run_cmd(args, cwd, timeout=600, env=None):
    """Foreground subprocess; never backgrounded."""
    try:
        p = subprocess.run(args, cwd=str(cwd), capture_output=True, text=True, timeout=timeout,
                           env=env or os.environ.copy(), stdin=subprocess.DEVNULL)
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired as exc:
        return 124, str(exc.stdout or ""), f"timeout after {timeout}s"
    except FileNotFoundError as exc:
        return 127, "", str(exc)


if __name__ == "__main__" and VS.SELF_CHECKS_FLAG in sys.argv:
    sys.exit(VS.run_self_checks(self_checks, SPRINT_KEY, __file__))

from _db_connect import admin_dsn, connect, hydrate, resolve_dsn  # noqa: E402

hydrate()  # BEFORE main is imported (Settings is lru_cached)

import httpx  # noqa: E402

import _authz1_common as C  # noqa: E402

OPERATOR_DSN_VAR = "DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP"
TAG = "portentry1verify"
NAME = "PE1V "            # reserved fixture name prefix (asserted to match no real row)
FIX_CUSIP = "PE1V00009"   # a fixture CUSIP shape no real security carries (asserted)
FIX_TICKER = "PEVXX"


def _id(n: int) -> UUID:
    return UUID(f"99000000-0000-0000-0000-00000054{n:04d}")


ORG_A, ORG_B = _id(1), _id(2)
FIXTURE_ORGS = [ORG_A, ORG_B]
MISSING = _id(9999)
FIX_SEC = _id(900)        # the fixture platform-master security (this script's, never the product's)

ORGADMIN_PERMS = frozenset({
    "manage_members", "manage_org_settings", "view_members", "view_portfolio", "view_dashboard", "view_held_away",
    "manage_held_away", "approve_held_away"})
SUPPORT_PERMS = frozenset({"review_agent_proposals", "view_community", "view_compliance", "view_dashboard",
                           "view_deals", "view_members", "view_portfolio", "view_workflow_runs", "edit_crm_contact"})
U_SUPER, U_SUPER2, U_OA, U_SUPPORT, U_MEMBER, U_OTHER = _id(10), _id(11), _id(12), _id(13), _id(14), _id(15)
USERS = {
    U_SUPER: (ORG_A, "super_admin", "super", "super_admin", frozenset(C.SUPER_ADMIN_PERMS)),
    U_SUPER2: (ORG_A, "super_admin", "super2", "super_admin", frozenset(C.SUPER_ADMIN_PERMS)),
    U_OA: (ORG_A, "org_admin", "orgadmin", "org_admin", ORGADMIN_PERMS),
    U_SUPPORT: (ORG_A, "member", "support", "support_staff", SUPPORT_PERMS),
    U_MEMBER: (ORG_A, "member", "member", "member", frozenset(C.MEMBER_PERMS)),
    U_OTHER: (ORG_B, "member", "other", "member", frozenset(C.MEMBER_PERMS)),
}
E = {
    "pat": (_id(30), ORG_A, "individual", "Patriarch"), "trust": (_id(31), ORG_A, "trust", "Family Trust"),
    "llc": (_id(32), ORG_A, "llc", "Holdings LLC"), "lp": (_id(33), ORG_A, "lp", "Family LP"),
    "found": (_id(34), ORG_A, "foundation", "Foundation"), "ins": (_id(35), ORG_A, "corporation", "Insurer Co"),
    "mem": (_id(36), ORG_A, "individual", "Member Self"), "other_fam": (_id(37), ORG_A, "individual", "Other Family"),
    "orgb": (_id(38), ORG_B, "individual", "Org B Person"), "spouse": (_id(39), ORG_A, "individual", "Spouse"),
}
HH_A = _id(60)
ACC_HA, ACC_INT, ACC_MEM = _id(70), _id(71), _id(72)
AS_OF = date.today()

COUNTED = ("public.users", "public.organizations", "public.entities", "public.entity_relationships",
           "public.entity_attributes", "public.entity_addresses", "public.entity_notes", "public.households",
           "public.household_memberships", "public.accounts", "public.config", "public.entity_holdings",
           "public.role_permissions", "public.profile_permissions", "portfolio.assets", "portfolio.positions",
           "portfolio.valuations", "portfolio.commitments", "portfolio.transactions", "portfolio.asset_relinks",
           "portfolio.provisional_imports", "portfolio.provisional_positions", "portfolio.rollup_requests",
           "portfolio.securities_global", "public.held_away_imports", "public.audit_log")
REAL_FINGERPRINTED = ("public.entities", "public.entity_relationships", "public.config", "public.role_permissions",
                      "portfolio.assets", "portfolio.positions", "public.organizations")

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


def sub_of(uid: UUID) -> str:
    return f"auth0|{TAG}-{USERS[uid][2]}"


def email_of(uid: UUID) -> str:
    return f"{TAG}-{USERS[uid][2]}@test.local"


def eid(k: str) -> UUID:
    return E[k][0]


def dumps(x) -> str:
    """For DISPLAY in a check's detail only — truncated at 400 characters."""
    return json.dumps(x, ensure_ascii=False, default=str)[:400]


def body_text(x) -> str:
    """The FULL serialised body, for every containment assertion (never dumps())."""
    return json.dumps(x, ensure_ascii=False, default=str)


# ═══ DB helpers (RLS context set explicitly on every read) ══════════════════════════════════════════════════════
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
    return {t: int(await platform(conn, f"SELECT count(*) FROM {t}", fetch="fetchval")) for t in COUNTED}


async def pk_of(conn, table: str) -> list[str]:
    rows = await platform(conn, "SELECT a.attname FROM pg_constraint c JOIN pg_attribute a ON a.attrelid = c.conrelid "
                                "AND a.attnum = ANY(c.conkey) WHERE c.conrelid = $1::regclass AND c.contype = 'p' "
                                "ORDER BY array_position(c.conkey, a.attnum)", table)
    return [r["attname"] for r in rows]


async def fingerprint(conn, table: str, *, where: str = "true", args=()) -> tuple[int, str]:
    """Count + md5 of every row keyed by the table's real primary key. No activity column is excluded: none of these
    tables is written by real sign-in activity."""
    pk = await pk_of(conn, table)
    if not pk:
        raise RuntimeError(f"{table} has no primary key in the catalog")
    key = " || ',' || ".join(f'"{c}"::text' for c in pk)
    row = await platform(conn, f"SELECT count(*) AS n, md5(COALESCE(string_agg(to_jsonb(t)::text, '|' "
                               f"ORDER BY {key}), '')) AS h FROM {table} t WHERE {where}", *args, fetch="fetchrow")
    return int(row["n"]), row["h"]


async def master_print(conn) -> tuple:
    """The platform master (securities + identifiers), EXCLUDING this script's own fixture security."""
    a = await fingerprint(conn, "portfolio.securities_global", where="id <> $1::uuid", args=(str(FIX_SEC),))
    b = await fingerprint(conn, "portfolio.securities_global_identifiers",
                          where="global_security_id <> $1::uuid", args=(str(FIX_SEC),))
    return a, b


async def operator_conn():
    dsn, how = await resolve_dsn(OPERATOR_DSN_VAR)
    if not dsn:
        raise RuntimeError(f"no operator DSN for the fixture teardown path: {how}")
    return await connect(dsn)


async def teardown(_conn=None) -> list[str]:
    """Fixture rows only — fixture orgs and users by id (every org_id table: config, entities, accounts, assets,
    positions, valuations, commitments, transactions, imports, provisional rows, rollup requests, holdings,
    liabilities, audit rows, workflow / task rows), the fixture roles' permission grants (through roles), the
    fixture security in the platform master by id (no org_id there)."""
    users = list(USERS)
    op = await operator_conn()
    try:
        plan = {
            "public.audit_log": ("org_id = ANY($1::uuid[]) OR user_id = ANY($2::uuid[])", [FIXTURE_ORGS, users]),
            "public.household_memberships": ("household_id = ANY($1::uuid[])", [[HH_A]]),
            "public.role_permissions": ("role_id IN (SELECT id FROM public.roles WHERE org_id = ANY($1::uuid[]))",
                                        [FIXTURE_ORGS]),
            "portfolio.securities_global_identifiers": ("global_security_id = $1::uuid", [FIX_SEC]),
            "portfolio.securities_global": ("id = $1::uuid", [FIX_SEC]),
        }
        res = await VC.cleanup(op, orgs=FIXTURE_ORGS, users=users, user_sub_like=f"%{TAG}%", plan=plan,
                               label="verify_portentry1")
        return res["log"]
    finally:
        await op.close()


async def leftovers(conn) -> int:
    op = await operator_conn()
    try:
        return await VC.leftover_count(op, orgs=FIXTURE_ORGS, users=list(USERS), user_sub_like=f"%{TAG}%", extra=[
            ("SELECT count(*) FROM public.household_memberships WHERE household_id = ANY($1::uuid[])", [[HH_A]]),
            ("SELECT count(*) FROM portfolio.securities_global WHERE id = $1::uuid", [FIX_SEC])])
    finally:
        await op.close()


async def cleanup_only(conn) -> int:
    log: list[str] = []
    try:
        log = await teardown(conn)
    except Exception as exc:  # noqa: BLE001
        check(False, "cleanup raised", f"{type(exc).__name__}: {exc}")
        traceback.print_exc()
    left = await leftovers(conn)
    print(f"LEFTOVERS: {left}")
    print(f"[INFO] cleanup removed: {log}")
    await conn.close()
    check(left == 0, "--cleanup-only: zero fixture rows remain", f"{left}")
    print(f"\nTOTAL: {_n_pass} PASS, {_n_fail} FAIL")
    return 0 if _n_fail == 0 else 1


# ═══ Catalog + seed ═════════════════════════════════════════════════════════════════════════════════════════════
def _allowed(defn: str) -> list[str]:
    return re.findall(r"'([^']+)'::text", defn or "")


ACCOUNT_VALUES: dict[str, str] = {}


async def y_catalog(conn):
    section("[Y] Constraints read from the catalog before seeding; reserved fixture prefixes match no real row")
    cons = {}
    for t in ("public.accounts", "portfolio.assets", "portfolio.positions", "portfolio.valuations",
              "portfolio.commitments", "portfolio.provisional_imports", "portfolio.rollup_requests", "public.config"):
        for r in await platform(conn, "SELECT conname, contype, pg_get_constraintdef(oid) AS d FROM pg_constraint "
                                      "WHERE conrelid = $1::regclass AND contype IN ('c', 'u', 'f')", t):
            cons[(t, r["conname"])] = r["d"]
    check(bool(("portfolio.positions", "positions_authority_chk") in cons
               and "stated" in cons[("portfolio.positions", "positions_authority_chk")]
               and "manual" in cons[("portfolio.positions", "positions_source_chk")]),
          "positions CHECKs admit authority 'stated' and source 'manual' (the provisional import's marks)")
    check(bool(("portfolio.provisional_imports", "provisional_imports_checker_chk") in cons),
          "provisional_imports carries the maker ≠ checker CHECK")
    check(bool(("public.config", "config_org_id_config_key_key") in cons), "config UNIQUE (org_id, config_key)")
    for col in ("registration_type", "tax_status", "custodian_code"):
        defs = [d for (t, n), d in cons.items() if t == "public.accounts" and col in d and d.startswith("CHECK")]
        vals = _allowed(defs[0]) if defs else []
        ACCOUNT_VALUES[col] = vals[0] if vals else {"registration_type": "individual", "tax_status": "taxable",
                                                    "custodian_code": "ALTRUIST"}[col]
    find("accounts seeded with catalog-legal values", f"{ACCOUNT_VALUES}")
    real = await platform(conn, "SELECT (SELECT count(*) FROM entities WHERE display_name LIKE $1 AND NOT (org_id = "
                                "ANY($4::uuid[]))) + (SELECT count(*) FROM users WHERE auth0_sub LIKE $2 AND NOT (id = "
                                "ANY($5::uuid[]))) + (SELECT count(*) FROM organizations WHERE slug LIKE $3 AND NOT "
                                "(id = ANY($4::uuid[]))) + (SELECT count(*) FROM portfolio.securities_global_identifiers "
                                "WHERE id_value IN ($6, $7) AND global_security_id <> $8::uuid)",
                          NAME + "%", f"%{TAG}%", f"{TAG}%", FIXTURE_ORGS, list(USERS), FIX_CUSIP, FIX_TICKER,
                          str(FIX_SEC), fetch="fetchval")
    check(real == 0, "the reserved fixture prefixes / identifiers match no real row", f"{real}")


async def seed(conn) -> None:
    from services import rbac

    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        for org, nm in ((ORG_A, "a"), (ORG_B, "b")):
            await conn.execute("INSERT INTO organizations (id, name, slug, is_test) VALUES ($1, $2, $3, true)",
                               org, f"PORTENTRY1 Verify Org {nm.upper()}", f"{TAG}-{nm}")
        perm_ids = {r["name"]: r["id"] for r in await conn.fetch("SELECT id, name FROM permissions")}
        missing = sorted(set().union(*(v[4] for v in USERS.values())) - set(perm_ids))
        if missing:
            raise RuntimeError(f"permission catalog lacks {missing}")
        roles = {}
        for uid, (org, _a, _n, rname, perms) in USERS.items():
            if (org, rname) not in roles:
                rid = await rbac.ensure_role(conn, org, rname, f"PORTENTRY1 fixture {rname}")
                for p in perms:
                    await conn.execute("INSERT INTO role_permissions (role_id, permission_id) VALUES ($1, $2) "
                                       "ON CONFLICT DO NOTHING", rid, perm_ids[p])
                roles[(org, rname)] = rid
        for uid, (org, account_role, nm, rname, _p) in USERS.items():
            await conn.execute(
                "INSERT INTO users (id, org_id, email, full_name, auth0_sub, role, status, is_active, is_test) "
                "VALUES ($1, $2, $3, $4, $5, $6, 'active', true, true)",
                uid, org, email_of(uid), f"PORTENTRY1 {nm.title()}", sub_of(uid), account_role)
            await conn.execute("INSERT INTO user_roles (user_id, role_id) VALUES ($1, $2)", uid, roles[(org, rname)])
        for k, (i, org, etype, name) in E.items():
            await conn.execute("INSERT INTO entities (id, org_id, entity_type, display_name, status) "
                               "VALUES ($1, $2, $3::entity_type, $4, 'active')", i, org, etype, NAME + name)
        # the family: trust owns 100% of the LLC; the patriarch and the trust are the household
        await conn.execute("INSERT INTO entity_relationships (org_id, from_entity_id, to_entity_id, relationship_type, "
                           "ownership_pct) VALUES ($1, $2, $3, 'ownership', 100)", ORG_A, eid("trust"), eid("llc"))
        await conn.execute("INSERT INTO households (id, org_id, name) VALUES ($1, $2, $3)", HH_A, ORG_A,
                           NAME + "household")
        for k in ("pat", "trust"):
            await conn.execute("INSERT INTO household_memberships (household_id, entity_id) VALUES ($1, $2)", HH_A,
                               eid(k))
        await conn.execute("INSERT INTO delegate_grants (org_id, principal_entity_id, delegate_user_id, scope) "
                           "VALUES ($1, $2, $3, 'view_only')", ORG_A, eid("mem"), U_MEMBER)
        await conn.execute("INSERT INTO delegate_grants (org_id, principal_entity_id, delegate_user_id, scope) "
                           "VALUES ($1, $2, $3, 'view_only')", ORG_B, eid("orgb"), U_OTHER)
        for aid, mask, owner, held in ((ACC_HA, "****5401", eid("pat"), True), (ACC_INT, "****5402", eid("llc"), False),
                                       (ACC_MEM, "****5403", eid("mem"), True)):
            await conn.execute(
                "INSERT INTO accounts (id, org_id, account_number_masked, account_number_hash, custodian_code, "
                "registration_type, tax_status, primary_entity_id, is_held_away) VALUES ($1, $2, $3, $4, $5, $6, $7, "
                "$8, $9)", aid, ORG_A, mask, hashlib.sha256(f"{TAG}{mask}".encode()).hexdigest(),
                ACCOUNT_VALUES["custodian_code"], ACCOUNT_VALUES["registration_type"], ACCOUNT_VALUES["tax_status"],
                owner, held)
        # this script's fixture security in the platform master (the relink target) — NEVER the product's
        await conn.execute("INSERT INTO portfolio.securities_global (id, name, security_type, canonical_id) "
                           "VALUES ($1, $2, 'fund', $1)", FIX_SEC, NAME + "Fixture Muni Fund")


async def y_fixtures(conn):
    section("[Y] Fixture orgs and users are is_test")
    o = await platform(conn, "SELECT count(*) FROM organizations WHERE id = ANY($1::uuid[]) AND is_test", FIXTURE_ORGS,
                       fetch="fetchval")
    u = await platform(conn, "SELECT count(*) FROM users WHERE id = ANY($1::uuid[]) AND is_test", list(USERS),
                       fetch="fetchval")
    check(bool(o == 2 and u == len(USERS)), "both fixture orgs and every fixture user are is_test = true", f"{o}/{u}")


# ═══ The real app, token stubbed by bearer string ═══════════════════════════════════════════════════════════════
CLAIMS_BY_TOKEN: dict[str, dict] = {}
NETWORK_CALLS: list = []


def token_for(uid: UUID) -> str:
    key = f"t-{TAG}-{uid}"
    CLAIMS_BY_TOKEN[key] = {"sub": sub_of(uid), "org_id": str(USERS[uid][0])}
    return key


async def api(uid, method, path, body=None, timeout=120.0, files=None, data=None):
    import main

    kw = {"headers": {"Authorization": f"Bearer {token_for(uid)}"}}
    if body is not None:
        kw["json"] = body
    if files is not None:
        kw["files"] = files
        kw["data"] = data or {}
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
    """Each stub matches the CURRENT signature of what it replaces (read at install time)."""
    import inspect

    import main
    from services import storage
    from services import users as users_mod

    main.verify_token = lambda tok: dict(CLAIMS_BY_TOKEN[tok])
    have = list(inspect.signature(users_mod.fetch_auth0_profile).parameters)
    if len(have) != 2:
        raise RuntimeError(f"fetch_auth0_profile signature drifted: {have}")

    async def no_profile(request, c):
        return None

    users_mod.fetch_auth0_profile = no_profile

    def no_r2(*a, **k):
        NETWORK_CALLS.append("r2")
        raise AssertionError("verify: R2 must never be called")

    storage.get_s3_client = no_r2


# ═══ Sections ═══════════════════════════════════════════════════════════════════════════════════════════════════
async def y_role_is_real(conn):
    section("[Y] The verify's DB role is the app role, not a bypass")
    r = await conn.fetchrow("SELECT current_user AS u, rolbypassrls FROM pg_roles WHERE rolname = current_user")
    check(bool(r["rolbypassrls"] is False), "the verify connection does not bypass RLS (rolbypassrls = false)",
          f"{r['u']}")


async def y_vocab(conn):
    section("[Y] Task 2 — CRM vocabularies from the config table; values save and re-read; existing statuses unchanged")
    code, v = await api(U_OA, "GET", "/api/v1/crm/vocabularies")
    voc = (v or {}).get("vocabularies") or {}
    sts = {s["value"] for s in voc.get("entity_statuses") or []}
    check(bool(code == 200 and {"client", "contact", "prospect", "active", "inactive", "archived"} <= sts),
          "the envelope serves client / contact beside the existing statuses", f"HTTP {code}; {sorted(sts)}")
    mar = [m["value"] for m in voc.get("marital_statuses") or []]
    check(mar == ["single", "married", "divorced", "widowed", "separated", "domestic_partnership"],
          "marital status vocabulary (six, in order)", f"{mar}")
    llc = [o["key"] for o in (voc.get("sub_types_by_type") or {}).get("llc") or []]
    check(llc == ["holding", "real_estate", "operating", "collectibles", "investment", "other"],
          "LLC sub-types from config", f"{llc}")
    seeded = await scoped(conn, ORG_A, "SELECT count(*) FROM config WHERE org_id = $1 AND category = ANY($2::text[])",
                          ORG_A, ["crm_entity_statuses", "crm_marital_statuses", "crm_sub_types"], fetch="fetchval")
    check(seeded > 0, "a new (fixture) org got its CRM labels copied on first read (crm_seed_org_config)", f"{seeded}")
    before = await platform(conn, "SELECT status, count(*) AS n FROM entities WHERE NOT (org_id = ANY($1::uuid[])) "
                                  "AND valid_to IS NULL AND system_to IS NULL GROUP BY 1 ORDER BY 1", FIXTURE_ORGS)
    c1, r1 = await api(U_OA, "PUT", f"/api/v1/entities/{eid('pat')}", {"status": "client", "marital_status": "married"})
    c2, r2 = await api(U_OA, "PUT", f"/api/v1/entities/{eid('llc')}", {"sub_type": "Real estate"})
    c3, r3 = await api(U_OA, "POST", "/api/v1/entities", {"entity_type": "individual", "display_name": NAME + "Contact",
                                                         "status": "contact", "marital_status": "domestic_partnership"})
    row = await scoped(conn, ORG_A, "SELECT status, marital_status FROM entities WHERE id = $1 AND valid_to IS NULL "
                                    "AND system_to IS NULL", eid("pat"), fetch="fetchrow")
    sub = await scoped(conn, ORG_A, "SELECT sub_type FROM entities WHERE id = $1 AND valid_to IS NULL AND system_to "
                                    "IS NULL", eid("llc"), fetch="fetchval")
    new = await scoped(conn, ORG_A, "SELECT status, marital_status FROM entities WHERE display_name = $1 AND "
                                    "valid_to IS NULL AND system_to IS NULL", NAME + "Contact", fetch="fetchrow")
    check(bool(c1 == 200 and row and row["status"] == "client" and row["marital_status"] == "married"),
          "status 'client' + marital 'married' save and re-read (independent read)", f"{c1} {dumps(r1)}; {row}")
    check(bool(c2 == 200 and sub == "Real estate"), "LLC sub-type 'Real estate' saves and re-reads", f"{c2}; {sub}")
    check(bool(c3 == 201 and new and new["status"] == "contact" and new["marital_status"] == "domestic_partnership"),
          "a new record created as 'contact' / domestic partnership", f"{c3} {dumps(r3)}; {new}")
    c4, r4 = await api(U_OA, "PUT", f"/api/v1/entities/{eid('pat')}", {"status": "vip", "marital_status": "engaged"})
    c5, r5 = await api(U_OA, "PUT", f"/api/v1/entities/{eid('llc')}", {"sub_type": "Spaceport"})
    still = await scoped(conn, ORG_A, "SELECT status FROM entities WHERE id = $1 AND valid_to IS NULL AND system_to IS "
                                      "NULL", eid("pat"), fetch="fetchval")
    check(bool(c4 == 422 and "vip" in body_text(r4) and "engaged" in body_text(r4) and still == "client"),
          "an unknown status / marital status is refused (422, every error), nothing written", f"{c4} {dumps(r4)}")
    check(bool(c5 == 422 and "Spaceport" in body_text(r5)), "an unknown LLC sub-type is refused", f"{c5} {dumps(r5)}")
    lst_c, lst = await api(U_OA, "GET", "/api/v1/entities?status=client&limit=500")
    ids = {x["id"] for x in lst} if isinstance(lst, list) else set()
    check(bool(lst_c == 200 and str(eid("pat")) in ids and str(eid("llc")) not in ids),
          "the CRM list's status filter narrows to client (and excludes the active LLC)", f"{lst_c}; {len(ids)}")
    after = await platform(conn, "SELECT status, count(*) AS n FROM entities WHERE NOT (org_id = ANY($1::uuid[])) "
                                 "AND valid_to IS NULL AND system_to IS NULL GROUP BY 1 ORDER BY 1", FIXTURE_ORGS)
    check([tuple(r) for r in before] == [tuple(r) for r in after], "every REAL record's status is unchanged",
          f"{[tuple(r) for r in after]}")
    # detail registry keys
    c6, r6 = await api(U_OA, "PUT", f"/api/v1/entities/{eid('lp')}/details", {"values": {"fund_role": "family_partnership"}})
    c7, r7 = await api(U_OA, "PUT", f"/api/v1/entities/{eid('found')}/details", {"values": {"ein_status": "501c3"}})
    c8, r8 = await api(U_OA, "PUT", f"/api/v1/entities/{eid('trust')}/details", {"values": {
        "trust_type": "ilit", "policy_insurer": str(eid("ins")), "policy_type": "survivorship_ul",
        "policy_face_amount": "25000000", "policy_insureds": [str(eid("pat")), str(eid("spouse"))],
        "policy_number_masked": "POL-****77"}})
    attrs = {r["attribute_key"]: r["attribute_value"] for r in await scoped(
        conn, ORG_A, "SELECT attribute_key, attribute_value FROM entity_attributes WHERE org_id = $1 AND entity_id = "
                     "ANY($2::uuid[]) AND valid_to IS NULL AND system_to IS NULL", ORG_A,
        [eid("lp"), eid("found"), eid("trust")])}
    check(bool(c6 == 200 and attrs.get("detail.fund_role") == "family_partnership"), "LP fund_role saves + re-reads",
          f"{c6} {dumps(r6)}")
    check(bool(c7 == 200 and attrs.get("detail.ein_status") == "501c3"), "foundation ein_status saves + re-reads",
          f"{c7} {dumps(r7)}")
    ins = json.loads(attrs.get("detail.policy_insureds") or "[]")
    check(bool(c8 == 200 and attrs.get("detail.policy_face_amount") == "25000000"
               and sorted(ins) == sorted([str(eid("pat")), str(eid("spouse"))])
               and attrs.get("detail.policy_number_masked") == "POL-****77"
               and attrs.get("detail.policy_insurer") == str(eid("ins"))),
          "ILIT policy group (insurer + insureds as CRM records, face amount, masked number) saves + re-reads",
          f"{c8} {dumps(r8)}")
    c9, r9 = await api(U_OA, "PUT", f"/api/v1/entities/{eid('trust')}/details",
                       {"values": {"policy_number_masked": "POL-12345677"}})
    check(bool(c9 == 422 and "MASKED" in body_text(r9)), "a full policy number is refused (masked only)",
          f"{c9} {dumps(r9)}")


async def y_gates(conn):
    section("[Y] Task 3 — ownership: internal + manage_members; edit_crm_contact is narrow")
    path = f"/api/v1/entities/{eid('trust')}/ownership"
    body = {"direction": "owns", "counterparty_id": str(eid("lp")), "ownership_pct": 40}
    res = {}
    for name, uid in (("orgadmin", U_OA), ("support", U_SUPPORT), ("member", U_MEMBER)):
        res[name] = await api(uid, "POST", path, body)
    n = await scoped(conn, ORG_A, "SELECT count(*) FROM entity_relationships WHERE from_entity_id = $1 AND "
                                  "to_entity_id = $2 AND relationship_type = 'ownership' AND valid_to IS NULL",
                     eid("trust"), eid("lp"), fetch="fetchval")
    check(bool(res["orgadmin"][0] == 201 and n == 1), "org admin (internal + manage_members) writes ownership % "
          "(one row, re-read)", f"{res['orgadmin'][0]} {dumps(res['orgadmin'][1])}; rows {n}")
    check(bool(res["support"][0] == 403 and "manage_members" in body_text(res["support"][1])),
          "support staff (no manage_members) refused on the IDENTICAL request, naming manage_members",
          f"{res['support'][0]} {dumps(res['support'][1])}")
    check(bool(res["member"][0] == 403), "member refused on the identical request", f"{res['member'][0]}")
    # edit_crm_contact: a note + an address + phone/email as support staff
    cn, rn = await api(U_SUPPORT, "POST", f"/api/v1/entities/{eid('pat')}/notes",
                       {"note_text": "PE1V support note", "note_type": "meeting"})
    ca, ra = await api(U_SUPPORT, "POST", f"/api/v1/entities/{eid('pat')}/addresses",
                       {"address_type": "primary_residence", "street1": "1 Verify Way", "city": "Greenwich",
                        "state": "CT", "postal_code": "06830", "country": "US"})
    cc, rc = await api(U_SUPPORT, "PUT", f"/api/v1/entities/{eid('pat')}/contact",
                       {"primary_email": "pe1v@test.local", "primary_phone": "+1 203 555 0100"})
    note = await scoped(conn, ORG_A, "SELECT count(*) FROM entity_notes WHERE entity_id = $1 AND note_text = $2",
                        eid("pat"), "PE1V support note", fetch="fetchval")
    addr = await scoped(conn, ORG_A, "SELECT count(*) FROM entity_addresses WHERE entity_id = $1 AND street1 = $2 AND "
                                     "valid_to IS NULL", eid("pat"), "1 Verify Way", fetch="fetchval")
    em = await scoped(conn, ORG_A, "SELECT primary_email FROM entities WHERE id = $1 AND valid_to IS NULL AND "
                                   "system_to IS NULL", eid("pat"), fetch="fetchval")
    check(bool(cn == 201 and note == 1), "support staff adds a note (re-read)", f"{cn} {dumps(rn)}")
    check(bool(ca == 201 and addr == 1), "support staff adds an address (re-read)", f"{ca} {dumps(ra)}")
    check(bool(cc == 200 and em == "pe1v@test.local"), "support staff sets the email / phone (re-read)",
          f"{cc} {dumps(rc)}")
    before = await fingerprint(conn, "public.entities", where="id = $1::uuid", args=(str(eid("pat")),))
    refused = {}
    refused["ownership"] = await api(U_SUPPORT, "POST", path, {**body, "counterparty_id": str(eid("found"))})
    refused["relationship"] = await api(U_SUPPORT, "POST", f"/api/v1/entities/{eid('pat')}/crm-relationships",
                                        {"relationship_type": "spouse", "other_entity_id": str(eid("spouse"))})
    refused["tax id"] = await api(U_SUPPORT, "POST", f"/api/v1/entities/{eid('pat')}/tax-ids",
                                  {"tax_id_type": "ssn", "tax_id_value": "000-00-0000"})
    refused["detail field"] = await api(U_SUPPORT, "PUT", f"/api/v1/entities/{eid('lp')}/details",
                                        {"values": {"fund_role": "fund"}})
    refused["status"] = await api(U_SUPPORT, "PUT", f"/api/v1/entities/{eid('pat')}", {"status": "archived"})
    refused["tag"] = await api(U_SUPPORT, "PUT", f"/api/v1/entities/{eid('pat')}", {"tags": ["vip"]})
    for what, (code, b) in refused.items():
        check(code == 403, f"support staff refused: {what}", f"{code} {dumps(b)}")
    c_x, b_x = await api(U_SUPPORT, "PUT", f"/api/v1/entities/{eid('pat')}/contact", {"status": "archived"})
    check(c_x == 422, "the contact route accepts nothing but email / phone (extra='forbid')", f"{c_x} {dumps(b_x)}")
    after = await fingerprint(conn, "public.entities", where="id = $1::uuid", args=(str(eid("pat")),))
    check(before == after, "the refused writes left the record byte-identical (status, tags …)", f"{before} → {after}")
    cm, bm = await api(U_MEMBER, "POST", f"/api/v1/entities/{eid('mem')}/notes", {"note_text": "x", "note_type": "meeting"})
    check(cm == 403, "a member never writes CRM notes", f"{cm} {dumps(bm)}")
    from services.authz import load_principal

    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        mp = await load_principal(conn, U_MEMBER)
        sp = await load_principal(conn, U_SUPPORT)
    check(bool("edit_crm_contact" not in mp.permissions and "edit_crm_contact" in sp.permissions),
          "edit_crm_contact: support staff holds it, a member never does", f"member {sorted(mp.permissions)[:5]}…")
    real_client = await platform(conn, "SELECT count(*) FROM role_permissions rp JOIN roles r ON r.id = rp.role_id JOIN "
                                       "permissions p ON p.id = rp.permission_id WHERE p.name = 'edit_crm_contact' AND "
                                       "r.name IN ('member', 'next_gen')", fetch="fetchval")
    check(real_client == 0, "no client role anywhere holds edit_crm_contact", f"{real_client}")
    real_support = await platform(conn, "SELECT count(*) FILTER (WHERE NOT EXISTS (SELECT 1 FROM role_permissions rp "
                                        "JOIN permissions p ON p.id = rp.permission_id WHERE rp.role_id = r.id AND "
                                        "p.name = 'edit_crm_contact')) FROM roles r WHERE r.name = 'support_staff' "
                                        "AND NOT (r.org_id = ANY($1::uuid[]))", FIXTURE_ORGS, fetch="fetchval")
    check(real_support == 0, "every real support_staff role holds edit_crm_contact (migration)", f"{real_support}")


async def _held_away_import(conn, rows):
    c, imp = await api(U_OA, "POST", f"/api/v1/held-away/accounts/{ACC_HA}/imports/manual",
                       {"as_of": AS_OF.isoformat(), "rows": rows})
    if c != 201:
        return c, imp, None
    c2, done = await api(U_SUPER, "POST", f"/api/v1/held-away/imports/{imp['id']}/approve", {"note": "verify"})
    return c2, imp, done


async def y_manual_positions(conn):
    section("[Y] Task 4 — manual positions for any holding; nothing created in the platform master")
    m0 = await master_print(conn)
    rows = [
        {"asset_kind": "mutual_fund", "name": NAME + "Muni Fund", "cusip": FIX_CUSIP, "quantity": "1000",
         "price": "13.90", "taxonomy_key": "taxonomy_sc_2"},
        {"asset_kind": "money_market", "name": NAME + "Treasury MMF", "quantity": "50000", "market_value": "50000",
         "taxonomy_key": "taxonomy_mc_8_1"},
        {"asset_kind": "municipal_bond", "name": NAME + "NYC GO 5% 2034", "quantity": "100000", "price": "1.02",
         "coupon": "5", "maturity": "2034-08-01", "taxonomy_key": "taxonomy_sc_2"},
        {"asset_kind": "cash", "quantity": "250000.55", "taxonomy_key": "taxonomy_mc_8_1"},
        {"asset_kind": "private", "name": NAME + "Club deal", "quantity": "1", "market_value": "400000",
         "internal_id": "PE1V-CLUB-1", "taxonomy_key": "taxonomy_sc_3"},
        {"asset_kind": "listed_option", "name": NAME + "PEVXX call", "quantity": "10", "price": "4.20",
         "underlying_ticker": "NOSUCHPE1", "option_type": "call", "strike": "100", "expiry": "2027-01-15",
         "taxonomy_key": "taxonomy_sc_1"},
        {"asset_kind": "digital_asset", "name": NAME + "Bitcoin", "ticker": "PE1VBTC", "quantity": "2.5",
         "price": "60000", "chain": "bitcoin", "taxonomy_key": "taxonomy_mc_7_3"},
    ]
    code, imp, done = await _held_away_import(conn, rows)
    check(bool(code == 200 and imp and imp.get("rows_rejected") == 0), "a draft of all seven holding types: nothing "
          "rejected; a second person approves (maker-checker as the held-away entry)",
          f"{code} {dumps(imp)} {dumps(done)}")
    c_self, b_self = await api(U_OA, "POST", f"/api/v1/held-away/accounts/{ACC_HA}/imports/manual",
                               {"as_of": AS_OF.isoformat(), "rows": rows[:1]})
    if c_self == 201:
        c2, b2 = await api(U_OA, "POST", f"/api/v1/held-away/imports/{b_self['id']}/approve", {})
        check(c2 == 403, "the person who entered an import never approves it", f"{c2} {dumps(b2)}")
        await api(U_SUPER, "POST", f"/api/v1/held-away/imports/{b_self['id']}/reject", {"note": "verify duplicate"})
    pos = await scoped(conn, ORG_A, "SELECT a.asset_type, a.global_security_id, a.name, p.quantity, p.market_value, "
                                    "a.manual_terms, p.taxonomy_key FROM portfolio.positions p JOIN portfolio.assets a "
                                    "ON a.id = p.asset_id WHERE p.account_id = $1 AND p.valid_to IS NULL AND "
                                    "p.system_to IS NULL", ACC_HA)
    kinds = {r["asset_type"]: r for r in pos}
    want = {"mutual_fund", "money_market", "municipal_bond", "cash", "private", "listed_option", "digital_asset"}
    check(set(kinds) == want, "every Task 4 type round-trips as a live position (re-read)", f"{sorted(kinds)}")
    check(bool(kinds and all(r["global_security_id"] is None for r in pos)),
          "every line is an ORG-scoped manual asset (global_security_id NULL — no master match exists)",
          f"{[(r['asset_type'], r['global_security_id']) for r in pos]}")
    muni = kinds.get("municipal_bond") or {}
    terms = muni.get("manual_terms") or {}
    terms = json.loads(terms) if isinstance(terms, str) else terms
    check(bool(muni and muni["market_value"] == Decimal("102000.00") and terms.get("coupon") == "5"
               and terms.get("maturity") == "2034-08-01"), "muni: value = quantity × price (exact), coupon + maturity "
          "kept", f"{muni}")
    cash = kinds.get("cash") or {}
    check(bool(cash and cash["market_value"] == Decimal("250000.55")), "cash: the amount is its value", f"{cash}")
    m1 = await master_print(conn)
    check(m0 == m1, "the platform master is untouched (count + checksum of securities and identifiers)",
          f"{m0} → {m1}")
    # relink: the fixture identifier now resolves (this script adds it to its OWN fixture security)
    await platform(conn, "INSERT INTO portfolio.securities_global_identifiers (global_security_id, id_type, id_value) "
                         "VALUES ($1, 'cusip', $2)", FIX_SEC, FIX_CUSIP, fetch="execute")
    code, rel = await api(U_OA, "POST", "/api/v1/portfolio/manual-assets/relink", {})
    linked = await scoped(conn, ORG_A, "SELECT count(*) FROM portfolio.assets WHERE org_id = $1 AND "
                                       "global_security_id = $2 AND valid_to IS NULL AND system_to IS NULL", ORG_A,
                          FIX_SEC, fetch="fetchval")
    report = await scoped(conn, ORG_A, "SELECT count(*) FROM portfolio.asset_relinks WHERE org_id = $1 AND "
                                       "global_security_id = $2", ORG_A, FIX_SEC, fetch="fetchval")
    archived = await scoped(conn, ORG_A, "SELECT count(*) FROM portfolio.assets WHERE org_id = $1 AND system_to IS NOT "
                                         "NULL AND global_security_id IS NULL AND asset_type = 'mutual_fund'", ORG_A,
                            fetch="fetchval")
    check(bool(code == 200 and (rel or {}).get("count") == 1 and linked == 1 and report == 1 and archived == 1),
          "the unmapped mutual fund relinks once its CUSIP resolves — system axis (prior version archived, id kept), "
          "REPORTED in asset_relinks", f"{code} {dumps(rel)}; linked {linked}, report {report}, archived {archived}")
    c_l, lst = await api(U_OA, "GET", "/api/v1/portfolio/manual-assets")
    check(bool(c_l == 200 and any(r.get("global_security_id") == str(FIX_SEC) for r in (lst.get("rows") or {})
                                  .get("relinks", []))), "the relink is listed on GET /portfolio/manual-assets",
          f"{c_l} {dumps(lst)}")
    m2 = await master_print(conn)
    check(m0 == m2, "the master is still untouched by the product after the relink", f"{m0} → {m2}")


async def y_direct_private(conn):
    section("[Y] Task 5 — direct assets + private investments; valuation history; unfunded; TA reads it")
    c, a = await api(U_OA, "POST", f"/api/v1/entities/{eid('trust')}/direct-assets", {
        "kind": "real_estate", "name": NAME + "Palm Beach house", "taxonomy_key": "taxonomy_sc_5",
        "ownership_pct": "100", "cost_basis": "3100000", "notes": "verify",
        "valuation": {"value": "4250000.00", "date": "2026-06-30", "method": "appraisal", "source": "Verify Appraisal"}})
    check(c == 201, "org admin adds a direct asset", f"{c} {dumps(a)}")
    aid = (a or {}).get("asset_id")
    c2, v2 = await api(U_OA, "POST", f"/api/v1/direct-assets/{aid}/valuations",
                       {"value": "4400000.00", "date": "2026-10-31", "method": "estimate"})
    vals = await scoped(conn, ORG_A, "SELECT value, valuation_method, status FROM portfolio.valuations WHERE asset_id = "
                                     "$1 AND valid_to IS NULL ORDER BY valuation_date", aid) if aid else []
    check(bool(c2 == 201 and [(r["value"], r["valuation_method"]) for r in vals]
               == [(Decimal("4250000.00"), "appraisal"), (Decimal("4400000.00"), "estimate")]),
          "a new valuation is a NEW row; the earlier one is kept (history)", f"{c2} {dumps(v2)}; {vals}")
    cl, lst = await api(U_OA, "GET", f"/api/v1/entities/{eid('trust')}/direct-assets")
    row = next((r for r in (lst or {}).get("rows", []) if r["asset_id"] == aid), {})
    check(bool(cl == 200 and row.get("owner_value") == "4400000.00" and len(row.get("valuations") or []) == 2),
          "the record lists it at the latest value with its history", f"{cl} {dumps(row)}")
    cs, bs = await api(U_SUPPORT, "POST", f"/api/v1/entities/{eid('trust')}/direct-assets", {
        "kind": "collectible", "name": NAME + "x", "valuation": {"value": "1", "date": "2026-01-01", "method": "estimate"}})
    check(bool(cs == 403 and "manage_members" in body_text(bs)), "support staff cannot add an asset", f"{cs}")
    cp, p = await api(U_OA, "POST", f"/api/v1/entities/{eid('llc')}/private-investments", {
        "kind": "fund", "name": NAME + "Growth Fund IV", "taxonomy_key": "taxonomy_sc_3",
        "commitment": {"amount": "5000000", "date": "2021-03-01", "vintage_year": 2021},
        "called_to_date": "3250000", "distributed_to_date": "400000",
        "nav": {"value": "4100000", "date": "2026-09-30"}})
    cid = (p or {}).get("commitment_id")
    cm = await scoped(conn, ORG_A, "SELECT commitment_amount, called_to_date, distributed_to_date, unfunded, "
                                   "vintage_year FROM portfolio.commitments WHERE id = $1", cid, fetch="fetchrow") \
        if cid else None
    check(bool(cp == 201 and cm and cm["called_to_date"] == Decimal("3250000") and cm["distributed_to_date"]
               == Decimal("400000") and cm["unfunded"] == Decimal("1750000") and cm["vintage_year"] == 2021),
          "private investment: commitment, called and distributed (ledger-derived), unfunded = 5,000,000 − 3,250,000 "
          "computed", f"{cp} {dumps(p)}; {dict(cm) if cm else None}")
    cf, f = await api(U_OA, "POST", f"/api/v1/private-investments/{cid}/flows",
                      {"type": "call", "amount": "250000", "date": "2026-10-15"})
    cm2 = await scoped(conn, ORG_A, "SELECT unfunded FROM portfolio.commitments WHERE id = $1", cid,
                       fetch="fetchval") if cid else None
    check(bool(cf == 201 and cm2 == Decimal("1500000")), "a later capital call re-derives unfunded (1,500,000)",
          f"{cf} {dumps(f)}; {cm2}")
    from services.ta_config import TA_STRATEGY_KEYS

    ct, t = await api(U_OA, "GET", f"/api/v1/modeling/ta/projection/{cid}?strategy_key={TA_STRATEGY_KEYS[0]}")
    check(bool(ct == 200 and Decimal(str(t.get("committed_capital"))) == Decimal("5000000")
               and Decimal(str(t.get("current_nav"))) == Decimal("4100000")),
          "the TA projection reads the commitment and the latest NAV (a valuation, not the stored market value)",
          f"{ct} {dumps(t)}")
    cm_, bm = await api(U_MEMBER, "GET", f"/api/v1/entities/{eid('trust')}/direct-assets")
    co, bo = await api(U_MEMBER, "GET", f"/api/v1/entities/{eid('mem')}/direct-assets")
    check(bool(cm_ == 404 and co == 200), "a member reads only their own family's assets (another family's → 404)",
          f"{cm_} / {co}")
    return cid


async def y_networth(conn):
    section("[Y] Task 7 — net worth current after writes (family-scoped); Recalculate; as-of")
    nw = (await api(U_OA, "GET", f"/api/v1/entities/{eid('trust')}/net-worth"))[1].get("rows") or {}
    want = Decimal("4400000.00") + Decimal("4100000.00")   # the house + the LLC's fund NAV, look-through
    check(bool(Decimal(nw.get("gross") or "0") == want and nw.get("holdings_as_of") == AS_OF.isoformat()),
          "the trust's gross = its house + its LLC's fund (look-through) WITHOUT a manual rollup; as-of shown",
          f"{dumps(nw)}")
    reqs = await scoped(conn, ORG_A, "SELECT status, entity_ids FROM portfolio.rollup_requests WHERE org_id = $1",
                        ORG_A)
    fam = {str(eid("trust")), str(eid("llc")), str(eid("pat"))}
    check(bool(reqs and all(r["status"] == "done" for r in reqs)
               and all(set(map(str, r["entity_ids"])) <= fam | {str(eid("lp"))} for r in reqs
                       if str(eid("trust")) in map(str, r["entity_ids"]))),
          "every write queued a FAMILY-scoped rollup (never org-wide), each done", f"{[dict(r) for r in reqs][:4]}")
    other = await scoped(conn, ORG_A, "SELECT count(*) FROM entity_holdings WHERE entity_id = $1", eid("other_fam"),
                         fetch="fetchval")
    check(other == 0, "an entity outside the family got no bucket", f"{other}")
    from services import portfolio_refresh as PR
    from services.database import get_pool

    pool = await get_pool()
    async with pool.acquire() as c:
        await c.execute("SELECT set_config('app.current_org_id', $1, true)", str(ORG_A))
        r1 = await PR.request_rollup(c, ORG_A, [eid("other_fam")], reason="debounce 1", delay_s=60)
        r2 = await PR.request_rollup(c, ORG_A, [eid("other_fam")], reason="debounce 2", delay_s=60)
        early = await PR.run_request(c, ORG_A, r1["id"])
    pend = await scoped(conn, ORG_A, "SELECT count(*) FROM portfolio.rollup_requests WHERE org_id = $1 AND status = "
                                     "'pending'", ORG_A, fetch="fetchval")
    check(bool(r1["id"] == r2["id"] and pend == 1 and early is None),
          "debounce: two writes inside the window are ONE pending request, not run before run_after",
          f"{r1['id']} / {r2['id']}; pending {pend}; early {early}")
    cr, rr = await api(U_OA, "POST", f"/api/v1/entities/{eid('pat')}/net-worth/recalculate", {})
    check(bool(cr == 200 and (rr.get("rollup") or {}).get("status") == "done"
               and (rr.get("net_worth") or {}).get("holdings_as_of") == AS_OF.isoformat()),
          "Recalculate (internal) runs the family now; the response carries the net worth and its as-of",
          f"{cr} {dumps(rr)}")
    cs, bs = await api(U_MEMBER, "POST", f"/api/v1/entities/{eid('mem')}/net-worth/recalculate", {})
    check(cs == 403, "a member cannot Recalculate", f"{cs} {dumps(bs)}")


async def y_import(conn):
    section("[Y] Task 6 — integrated import: preview, second-person approval, provisional, superseded by the feed")
    csv_text = ("account_mask,as_of_date,name,ticker,cusip,asset_class,sub_class,taxonomy_key,quantity,price,"
                "market_value,cost_basis\n"
                f"****5402,{AS_OF.isoformat()},{NAME}Total Market,PE1VTM,,Public equity,US total market ETF,"
                "taxonomy_sc_1,100,10.5,1050,900\n"
                f"****5402,{AS_OF.isoformat()},{NAME}SMA,,,Public equity,Direct-index SMA,taxonomy_sc_1,,,5000,\n"
                f"123456789,{AS_OF.isoformat()},{NAME}Bad,,,Cash,,,1,1,1,\n")
    m0 = await master_print(conn)
    cu, up = await api(U_SUPER, "POST", "/api/v1/admin/integrated-imports",
                       files={"file": ("int.csv", csv_text.encode(), "text/csv")})
    tot = (up or {}).get("totals") or {}
    check(bool(cu == 201 and up.get("status") == "draft" and tot.get("rows") == 2 and tot.get("rejected") == 1
               and tot.get("value") == "6050"), "upload → a DRAFT preview: 2 lines, the full-number line rejected, "
          "total 6,050", f"{cu} {dumps(up)}")
    rej = ((up or {}).get("preview") or {}).get("rejects") or []
    check(bool(rej and "MASK" in body_text(rej)), "a full account number is refused (mask only)", f"{rej}")
    n_draft = await scoped(conn, ORG_A, "SELECT count(*) FROM portfolio.positions WHERE account_id = $1", ACC_INT,
                           fetch="fetchval")
    check(n_draft == 0, "a draft writes no position", f"{n_draft}")
    iid = (up or {}).get("id")
    c_own, b_own = await api(U_SUPER, "POST", f"/api/v1/admin/integrated-imports/{iid}/approve", {})
    check(c_own == 403, "the uploader cannot approve their own import", f"{c_own} {dumps(b_own)}")
    c_oa, _ = await api(U_OA, "POST", f"/api/v1/admin/integrated-imports/{iid}/approve", {})
    check(c_oa == 403, "an org admin cannot (Super Admin screen)", f"{c_oa}")
    ca, ap = await api(U_SUPER2, "POST", f"/api/v1/admin/integrated-imports/{iid}/approve", {"note": "second person"})
    pos = await scoped(conn, ORG_A, "SELECT p.id, p.authority, p.source_system, p.market_value, p.asset_id, "
                                    "p.owner_entity_id, p.as_of_date FROM portfolio.positions p WHERE p.account_id = $1 "
                                    "AND p.valid_to IS NULL AND p.system_to IS NULL", ACC_INT)
    prov = await scoped(conn, ORG_A, "SELECT count(*) FROM portfolio.provisional_positions WHERE account_id = $1 AND "
                                     "valid_to IS NULL", ACC_INT, fetch="fetchval")
    check(bool(ca == 200 and len(pos) == 2 and prov == 2 and all(r["authority"] == "stated" and r["source_system"]
                                                                  == "manual" for r in pos)),
          "a second Super Admin approves: rows written as the provisional 'stated' source, listed as provisional",
          f"{ca} {dumps(ap)}; {[dict(r) for r in pos]}")
    st = await scoped(conn, ORG_A, "SELECT status, approved_by FROM portfolio.provisional_imports WHERE id = $1", iid,
                      fetch="fetchrow")
    check(bool(st and st["status"] == "approved" and st["approved_by"] == U_SUPER2), "the import records its checker",
          f"{dict(st) if st else None}")
    m1 = await master_print(conn)
    check(m0 == m1, "the import created nothing in the platform master", f"{m0} → {m1}")
    # the custodian feed arrives: a fixture altruist row on the same holding key, then the supersession
    first = pos[0] if pos else None
    if first:
        from services.integrated_import import supersede_for_account
        from services.portfolio_assets import create_position
        from services.database import get_pool

        pool = await get_pool()
        async with pool.acquire() as c:
            await create_position(c, org_id=str(ORG_A), owner_entity_id=str(first["owner_entity_id"]),
                                  asset_id=str(first["asset_id"]), as_of_date=first["as_of_date"],
                                  authority="custodial", source_system="altruist", ownership_basis="value",
                                  market_value=Decimal("1100"), account_id=str(ACC_INT))
            res = await supersede_for_account(c, ORG_A, ACC_INT, by_source="altruist")
        loser = await scoped(conn, ORG_A, "SELECT superseded_by_source, valid_to FROM portfolio.positions WHERE id = $1",
                             first["id"], fetch="fetchrow")
        left = await scoped(conn, ORG_A, "SELECT count(*) FROM portfolio.provisional_positions WHERE account_id = $1 "
                                         "AND valid_to IS NULL", ACC_INT, fetch="fetchval")
        live = await scoped(conn, ORG_A, "SELECT source_system FROM portfolio.positions WHERE account_id = $1 AND "
                                         "valid_to IS NULL AND system_to IS NULL", ACC_INT)
        check(bool(loser and loser["superseded_by_source"] == "altruist"),
              "portfolio_precedence: the feed's row WINS the shared holding key ('manual' ranks below 'altruist')",
              f"{dict(loser) if loser else None}; {res}")
        check(bool(left == 0 and [r["source_system"] for r in live] == ["altruist"] and loser["valid_to"] is not None),
              "every provisional row of the account is closed (kept, valid_to); only the feed's row is live",
              f"left {left}; live {[r['source_system'] for r in live]}")


async def y_isolation(conn):
    section("[Y] Member scope and org isolation both directions (rolbypassrls = false); demo protection intact")
    r = await conn.fetchrow("SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user")
    check(bool(r["rolbypassrls"] is False), "isolation is measured on a non-bypassing role")
    for t in ("portfolio.provisional_imports", "portfolio.rollup_requests", "portfolio.asset_relinks",
              "portfolio.provisional_positions"):
        a = await scoped(conn, ORG_A, f"SELECT count(*) FROM {t} WHERE org_id = $1", ORG_A, fetch="fetchval")
        b = await scoped(conn, ORG_B, f"SELECT count(*) FROM {t} WHERE org_id = $1", ORG_A, fetch="fetchval")
        check(bool(a > 0 and b == 0), f"RLS {t}: org A reads its rows, org B reads none of them", f"{a} / {b}")
    c1, _ = await api(U_OTHER, "GET", f"/api/v1/entities/{eid('trust')}/direct-assets")
    c2, _ = await api(U_OTHER, "GET", f"/api/v1/entities/{eid('llc')}/private-investments")
    check(bool(c1 == 404 and c2 == 404), "org B's member on org A's records → 404 (existence not disclosed)",
          f"{c1}/{c2}")
    c3, b3 = await api(U_OA, "GET", f"/api/v1/entities/{eid('orgb')}/direct-assets")
    check(c3 == 404, "org A's admin on org B's record → 404", f"{c3} {dumps(b3)}")
    trg = await platform(conn, "SELECT count(*) FROM pg_trigger WHERE tgname = 'crm_demo_delete_guard_trg'",
                         fetch="fetchval")
    check(bool(trg >= 9), "the demo delete guard is still installed on the CRM tables", f"{trg}")
    src = (HERE / "_verify_cleanup.py").read_text(encoding="utf-8")
    check("demo_refusals" in src or "demo_fixture_orgs" in src, "_verify_cleanup keeps its demo guard")


async def y_routes(conn):
    section("[Y] Routes — own routes via _verify_self; authz1_routes.py --check CURRENT")
    self_routes(check, find)
    code, out, err = run_cmd([sys.executable, str(HERE / "authz1_routes.py"), "--check"], REPO, timeout=600)
    check(bool(code == 0 and "CURRENT" in out), "authz1_routes.py --check: the route record is CURRENT",
          (out + err)[-200:])


async def y_front_end(conn):
    section("[Y] Front end — node --test tests/; npm run build")
    code, out, err = run_cmd(["node", "--test", "tests/"], WEB_DIR, timeout=900)
    m = re.search(r"^# fail (\d+)", out, re.M)
    check(bool(code == 0 and m is not None and m.group(1) == "0"), "node --test tests/ passes (0 fail)",
          (out + err)[-300:] if code else "")
    code, out, err = run_cmd(["npm", "run", "build"], WEB_DIR, timeout=1800)
    check(bool(code == 0), "npm run build exits 0", (out + err)[-400:] if code else "")


def regression():
    section("[Y] Regression — verify_crmdetail1.py ONCE, flat, AFTER teardown, VERIFY_NESTED=1, 2700 s")
    env = os.environ | {"VERIFY_NESTED": "1"}
    env.pop("PORTFOLIO_ROLLUP_DEBOUNCE_S", None)
    code, out, err = run_cmd([sys.executable, str(HERE / "verify_crmdetail1.py")], REPO, timeout=2700, env=env)
    m = re.search(r"TOTAL: (\d+) PASS, (\d+) FAIL", out)
    fails = [ln for ln in out.splitlines() if ln.startswith("[FAIL]")]
    check(bool(m is not None and m.group(2) == "0" and code == 0), "verify_crmdetail1.py: 0 FAIL",
          f"{m.group(0) if m else 'no TOTAL'}; {fails[:4]} {err[-200:] if not m else ''}")


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
    print(f"[INFO] database via {how}; today {date.today().isoformat()}")
    conn = await connect(dsn)
    install_stubs()
    if "--cleanup-only" in sys.argv:
        return await cleanup_only(conn)
    pre = real = master = None
    try:
        await run("pre-teardown", teardown, conn)
        pre = await counts(conn)
        real = {t: await fingerprint(conn, t) for t in REAL_FINGERPRINTED}
        master = await master_print(conn)
        await run("role", y_role_is_real, conn)
        await run("catalog", y_catalog, conn)
        await run("seed", seed, conn)
        await run("fixtures", y_fixtures, conn)
        section("[Y] Task 1 — findings 1a–1g")
        self_task1(check, find)
        await run("vocabularies", y_vocab, conn)
        await run("gates", y_gates, conn)
        await run("manual positions", y_manual_positions, conn)
        await run("direct + private", y_direct_private, conn)
        await run("net worth", y_networth, conn)
        await run("integrated import", y_import, conn)
        await run("isolation", y_isolation, conn)
        section("[Y] Task 8 — _verify_self")
        await run("verify_self", self_verify_self, check, find)
        section("[Y] Static — schema file, docs, generated CSV, regression set")
        await run("static", self_static, check, find)
        await run("routes", y_routes, conn)
        await run("front end", y_front_end, conn)
        check(not NETWORK_CALLS, "no network call was attempted", f"{NETWORK_CALLS}")
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
                    check(now == real[t], f"real {t} unchanged (count + checksum by primary key)",
                          f"{real[t][0]} → {now[0]} rows")
            if master is not None:
                check(await master_print(conn) == master, "the platform master is byte-identical to its pre-test state")
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
