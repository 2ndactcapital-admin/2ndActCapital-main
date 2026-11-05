"""verify_portentry2 — what HollisMuse hit onboarding the Olivo family (interim report 2026-11-05): legal entities as
members of an EXISTING household and as account owners (H-02 / G-02, the blocker), account fields + DVP (H-01, H-03,
H-04), permission-true CRM controls and menu (PERS-01, PERS-03), the ownership views agreeing (F-01, F-02, F-03), the
picker / dates / dialogs / detail keys / seasonal addresses / nav / live counts, and staff security search (P.1).

Pass/fail only, no prompts. Prints 'TOTAL: N PASS, M FAIL' and exits non-zero on any failure. AUTHZ_TEST_MODE=1 is set
before anything imports ``main``; secrets are hydrated from Doppler over HTTPS (``_db_connect.hydrate``). No network:
the Auth0 profile fetch and R2 are replaced by local stubs that match the CURRENT signatures.

Everything after the bearer token is the real app against the live database, in TWO fixture orgs
(99000000-…-000000550001 / 550002), ``is_test = true`` (asserted), as are the fixture users. The Olivo demo rows
(tag demo-olivo) and every real row are only READ: count + checksum before and after. Teardown at start and end via
_verify_cleanup by fixture id; every counted table is re-read for its exact pre-test count.

--self-checks-only: git / route-record / static checks only (no Doppler, DB, network or npm).
--cleanup-only: the teardown + leftover check only.
With VERIFY_NESTED=1 the regression section prints one '[SKIP] nested run'.
"""

from __future__ import annotations

import os as _authz_os
_authz_os.environ["AUTHZ_TEST_MODE"] = "1"  # AUTHZ1: set before main is imported
_authz_os.environ["PORTFOLIO_ROLLUP_DEBOUNCE_S"] = "0"

import asyncio
import hashlib
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
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

SPRINT_KEY = "portentry2"
BEFORE_REF = "ad97ea3"   # portentry2-work base: "olivo demo: Muse entry plan v3"

IM = "internal + any(manage_members)"
NEW_ROUTES = {
    ("PATCH", "/api/v1/admin/households/{household_id}/accounts/{account_id}"): IM,
    ("GET", "/api/v1/entities/{entity_id}/permissions"): "internal",
    ("GET", "/api/v1/securities/search"): "internal",
}
#: existing routes whose gate this sprint DELIBERATELY changed → their new gate (the household admin gate, PERMS1's)
CHANGED_ROUTES = {
    ("POST", "/api/v1/admin/households/{household_id}/members"): IM,
    ("DELETE", "/api/v1/admin/households/{household_id}/members/{entity_id}"): IM,
}
MUSE_IDS = ("H-02", "G-02", "H-01", "H-03", "H-04", "PERS-01", "PERS-03", "E4-01", "B11-03", "B11-01", "F-01",
            "F-02", "F-03", "P.1", "C-01", "D-01", "D-02", "D19-01", "D17-01", "E-01", "E-02", "B112-01", "G-01",
            "H-05")

# ── Task 1 — needles written by hand from the discovery; each must be present at the sprint's end. ──────────────
TASK1 = [
    ("apps/api/services/household_admin.py", "async def add_member(conn, *, org_id, actor_id, household_id, "
     "entity_id)", "1a: household_memberships (PK household_id, entity_id; FK entities; no type CHECK) — an entity "
     "may be a member in the DB; the gap was the UI + an unaudited, rbac-gated add route"),
    ("apps/api/services/household_admin.py", "async def owner_candidates(conn, org_id, household_id, member_ids)",
     "1b: the Owner list = household members (any type); Olivo's 14 members were all people"),
    ("apps/web/lib/portentry2.mjs", "export function lookthroughPercentMap(items)",
     "1c: /lookthrough returns 0–1 FRACTIONS, owners' effective_pct 0–100 — merged into one map"),
    ("apps/api/routers/entities.py", "def search_tokens(q: str | None) -> list[str]:",
     "1d: the picker searched one contiguous substring of display / legal name with is_active = true"),
    ("apps/web/components/ui/DateInput.jsx", "export default function DateInput(",
     "1e: native <input type=date> (a year spin-button) on the person / entity forms"),
    ("apps/api/services/record_permissions.py", "GATES: dict[str, tuple[str, tuple[str, ...]]] = {",
     "1f: the Ownership tab's Add / Edit / Remove had NO gate; the routes require internal + manage_members"),
    ("apps/api/services/security_search.py", "async def search(conn, q: str, *, limit: int = 20)",
     "1g: /portfolio/global-securities is view_portfolio (a member grant too); held-away was a free ticker box"),
]
TASK1_FINDS = [
    "1a — readers key on entity ids, never on type: _sum_holdings / liabilities (net worth), portentry household "
    "assets, portfolio_refresh, X-ray _visible_households (accounts.primary_entity_id ∈ the member closure), the member "
    "closure itself (delegate grants → resolve_entity_set subtree over ownership + beneficiary edges). Membership is "
    "MANY-TO-MANY by design (SOC Phase 3): an entity may sit in several households; money that must not double count "
    "uses entities.primary_household_id (billing), which this sprint does not touch.",
    "1b — add_account required membership; accounts.service_model is free text set by the custody import and read by "
    "altruist_one — offered as an optional field. Olivo's 8 accounts were UNKNOWN / UNKNOWN; is_billable defaulted on.",
    "1c — all three views read services.ownership_read (both tables). The tab's tree was the server-rendered prop, "
    "never refreshed after the tab's own writes (owners added there never reached it); its 'Owns' tree recursed "
    "(look-through mixed with direct: an entity reached twice showed twice).",
    "1d — the [value?.id] effect reset the box when the parent cleared the value; a chosen record's display came from "
    "f.display (stale after a pick) → the id.",
    "1e — ReferenceSelect was UNCONTROLLED with defaultValue applied before its options loaded → 'Select…'.",
    "1f — PERS-03: support staff's admin items (Workflows → Runs / Triggers: view_workflow_runs; Billing & Custody: "
    "internal + view_portfolio) each match a grant it holds — no change needed; Households is hidden from it.",
    "1g — the master admin screen stays Super Admin (routers/security_master); the new search is read-only.",
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
          "exactly the decided gate changes (household member add / remove → internal + manage_members)",
          f"{changed}")
    check(not own.removed, "no route removed", f"{own.removed}")
    drift = VS.still_gated(own)
    check(not drift, "the sprint's routes still carry the same gate today", f"{drift}")


def self_static(check, find):
    sql = _read("docs/portentry2_part1.sql")
    check(bool("account_registration_types" in sql and "account_tax_statuses" in sql and "crm_seed_org_config" in sql
               and "ADD COLUMN IF NOT EXISTS note" in sql and "DROP " not in sql.upper().replace("-- ", "")),
          "docs/portentry2_part1.sql saved (account vocabularies, seeding, address note) — additive only")
    check("verify_portentry2.py" in _read("scripts/regression_set.txt"), "Task 9: in scripts/regression_set.txt")
    status = _read("docs/PROJECT_STATUS.md")
    m_sec = re.search(r"\n## 0+\. PORTENTRY2 — .*?(?=\n## )", status, re.S)
    top = m_sec.group(0) if m_sec else ""
    missing = [i for i in MUSE_IDS if i not in top]
    check(bool(top and not missing), "Task 9: PROJECT_STATUS entry names every Muse finding with its status",
          f"missing {missing}")
    readme = _read("docs/demo/olivo/README.md")
    check(bool("26 legal entities" in readme and "Protection rule (crmdetail1)" in readme),
          "Task 9: Olivo README — the household now holds the 26 legal entities; the protection rule kept")
    tab = _read("apps/web/components/crm/tabs/OwnershipTab.jsx")
    check(bool("canEditOwnership(data)" in tab and "ownership-edges" in tab and "maxDepth={lookThrough" in tab
               and "item.notes" in tab), "Ownership tab: envelope-gated controls, self-refreshing tree, direct / "
          "look-through toggle, interest note")
    check(not any('type="date"' in _read(f"apps/web/components/crm/{p}") for p in (
        "EntityDetailsForm.jsx", "NewEntityForm.jsx", "RelationshipsPanel.jsx", "tabs/OwnershipTab.jsx",
        "tabs/NotesTab.jsx", "tabs/EmploymentTab.jsx", "DetailFieldsPanel.jsx")),
          "no native date input left on the person / entity forms (one DateInput)")
    check("maiden_name" in _read("apps/web/components/crm/NewEntityForm.jsx")
          and "date_of_death" in _read("apps/web/components/crm/NewEntityForm.jsx"),
          "B112-01: New person offers maiden name and date of death")
    check('"/portfolio/households", "Households", GATE_HOUSEHOLDS' in _read("apps/web/lib/menuVisibility.mjs"),
          "G-01: Households nav row")


def self_checks(check, find):
    self_task1(check, find)
    self_routes(check, find)
    self_static(check, find)


def run_cmd(args, cwd, timeout=600, env=None, stdin_text=None):
    """Foreground subprocess; never backgrounded."""
    try:
        p = subprocess.run(args, cwd=str(cwd), capture_output=True, text=True, timeout=timeout,
                           env=env or os.environ.copy(), input=stdin_text,
                           stdin=None if stdin_text is not None else subprocess.DEVNULL)
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
TAG = "portentry2verify"
NAME = "PE2V "            # reserved fixture name prefix (asserted to match no real row)


def _id(n: int) -> UUID:
    return UUID(f"99000000-0000-0000-0000-00000055{n:04d}")


ORG_A, ORG_B = _id(1), _id(2)
FIXTURE_ORGS = [ORG_A, ORG_B]
MISSING = _id(9999)

ORGADMIN_PERMS = frozenset({"manage_members", "manage_org_settings", "view_members", "view_portfolio",
                            "view_dashboard", "view_held_away", "manage_held_away", "approve_held_away",
                            "view_replication"})
SUPPORT_PERMS = frozenset({"review_agent_proposals", "view_community", "view_compliance", "view_dashboard",
                           "view_deals", "view_members", "view_portfolio", "view_workflow_runs", "edit_crm_contact"})
U_OA, U_SUPPORT, U_MEMBER, U_OB_ADMIN = _id(12), _id(13), _id(14), _id(15)
USERS = {
    U_OA: (ORG_A, "org_admin", "orgadmin", "org_admin", ORGADMIN_PERMS),
    U_SUPPORT: (ORG_A, "member", "support", "support_staff", SUPPORT_PERMS),
    U_MEMBER: (ORG_A, "member", "member", "member", frozenset(C.MEMBER_PERMS)),
    U_OB_ADMIN: (ORG_B, "org_admin", "obadmin", "org_admin", ORGADMIN_PERMS),
}
# key → (id, org, entity_type, display_name, extra columns)
E = {
    "pat": (_id(30), ORG_A, "individual", "Patriarch Olivo", {}),
    "marg": (_id(31), ORG_A, "individual", "Marguerite Meloni Olivo",
             {"first_name": "Marguerite", "middle_name": "Meloni", "surname": "Olivo"}),
    "ther": (_id(32), ORG_A, "individual", "Theresa Zucchini Olivo",
             {"first_name": "Theresa", "surname": "Olivo", "is_active": False, "status": "deceased",
              "date_of_death": date(2008, 2, 9)}),
    "trust": (_id(33), ORG_A, "trust", "Revocable Trust", {}),
    "llc": (_id(34), ORG_A, "llc", "Holdings LLC", {}),
    "lp": (_id(35), ORG_A, "lp", "Holdings LP", {}),
    "llc2": (_id(36), ORG_A, "llc", "Real Estate LLC", {}),
    "grat": (_id(37), ORG_A, "trust", "2019 GRAT", {}),
    "found": (_id(38), ORG_A, "foundation", "Family Foundation", {}),
    "acctrec": (_id(39), ORG_A, "account", "Account Record", {}),
    "child": (_id(40), ORG_A, "llc", "Stag's Hollow LLC", {}),
    "mem": (_id(41), ORG_A, "individual", "Member Self", {}),
    "orgb": (_id(42), ORG_B, "individual", "Org B Person", {}),
    **{f"own{i}": (_id(50 + i), ORG_A, "llc", f"Owner {i} LLC", {}) for i in range(1, 8)},
}
HH_A, HH_B = _id(60), _id(61)
LP_OWNER_PCTS = {1: 34.5, 2: 30, 3: 10, 4: 10, 5: 10, 6: 5, 7: 0.5}     # the Olivo Holdings LP shape: 7 owners, 100%
REL_TABLE_OWNERS = (1, 2, 3, 4, 5)       # in entity_relationships (with an interest note); 6, 7 in entity_ownership

COUNTED = ("public.users", "public.organizations", "public.entities", "public.entity_relationships",
           "public.entity_ownership", "public.entity_attributes", "public.entity_addresses", "public.households",
           "public.household_memberships", "public.accounts", "public.config", "public.entity_holdings",
           "public.delegate_grants", "public.role_permissions", "public.audit_log")
REAL_FINGERPRINTED = ("public.entities", "public.entity_relationships", "public.entity_ownership",
                      "public.households", "public.household_memberships", "public.accounts",
                      "public.organizations", "public.role_permissions")

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
    return json.dumps(x, ensure_ascii=False, default=str)[:400]


def body_text(x) -> str:
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
    """Count + md5 of every row keyed by the table's real primary key. Real rows only (fixture orgs excluded by the
    caller's ``where``); none of these tables is written by real sign-in activity, so no column is excluded —
    EXCEPT public.organizations / users, which this script does not fingerprint by activity column."""
    pk = await pk_of(conn, table)
    if not pk:
        raise RuntimeError(f"{table} has no primary key in the catalog")
    key = " || ',' || ".join(f'"{c}"::text' for c in pk)
    row = await platform(conn, f"SELECT count(*) AS n, md5(COALESCE(string_agg(to_jsonb(t)::text, '|' "
                               f"ORDER BY {key}), '')) AS h FROM {table} t WHERE {where}", *args, fetch="fetchrow")
    return int(row["n"]), row["h"]


def _real_where(table: str) -> str:
    if table == "public.household_memberships":
        return "household_id NOT IN (SELECT id FROM public.households WHERE org_id = ANY($1::uuid[]))"
    if table == "public.organizations":
        return "NOT (id = ANY($1::uuid[]))"
    if table == "public.role_permissions":
        return "role_id NOT IN (SELECT id FROM public.roles WHERE org_id = ANY($1::uuid[]))"
    return "NOT (org_id = ANY($1::uuid[]))"


async def real_prints(conn) -> dict:
    return {t: await fingerprint(conn, t, where=_real_where(t), args=(FIXTURE_ORGS,)) for t in REAL_FINGERPRINTED}


DEMO_SQL = {
    "entities": ("SELECT * FROM public.entities WHERE 'demo-olivo' = ANY(tags)", "id, system_from"),
    "relationships": ("SELECT * FROM public.entity_relationships r WHERE r.from_entity_id IN (SELECT id FROM "
                      "public.entities WHERE 'demo-olivo' = ANY(tags)) OR r.to_entity_id IN (SELECT id FROM "
                      "public.entities WHERE 'demo-olivo' = ANY(tags))", "id"),
    "households": ("SELECT * FROM public.households h WHERE public.crm_is_demo_household(h.id)", "id"),
    "memberships": ("SELECT * FROM public.household_memberships m WHERE public.crm_is_demo_household(m.household_id)",
                    "household_id, entity_id"),
    "accounts": ("SELECT * FROM public.accounts a WHERE a.household_id IN (SELECT id FROM public.households h WHERE "
                 "public.crm_is_demo_household(h.id))", "id"),
}


async def demo_prints(conn) -> dict:
    out = {}
    for k, (sql, order) in DEMO_SQL.items():
        r = await platform(conn, f"SELECT count(*) AS n, md5(COALESCE(string_agg(to_jsonb(t)::text, '|' ORDER BY "
                                 f"{', '.join('t.' + c.strip() for c in order.split(','))}), '')) AS h FROM ({sql}) t",
                           fetch="fetchrow")
        out[k] = (int(r["n"]), r["h"])
    return out


async def operator_conn():
    dsn, how = await resolve_dsn(OPERATOR_DSN_VAR)
    if not dsn:
        raise RuntimeError(f"no operator DSN for the fixture teardown path: {how}")
    return await connect(dsn)


async def teardown(_conn=None) -> list[str]:
    """Fixture rows only — fixture orgs and users by id (every org_id table: config, entities, relationships,
    ownership, addresses, attributes, holdings, accounts (live + archived versions), delegate grants, audit rows,
    authority drafts / tasks), the fixture households' memberships, the fixture roles' grants."""
    users = list(USERS)
    op = await operator_conn()
    try:
        plan = {
            "public.audit_log": ("org_id = ANY($1::uuid[]) OR user_id = ANY($2::uuid[])", [FIXTURE_ORGS, users]),
            "public.household_memberships": ("household_id = ANY($1::uuid[])", [[HH_A, HH_B]]),
            "public.role_permissions": ("role_id IN (SELECT id FROM public.roles WHERE org_id = ANY($1::uuid[]))",
                                        [FIXTURE_ORGS]),
        }
        res = await VC.cleanup(op, orgs=FIXTURE_ORGS, users=users, user_sub_like=f"%{TAG}%", plan=plan,
                               gucs=("execution.teardown",), label="verify_portentry2")
        return res["log"]
    finally:
        await op.close()


async def leftovers(conn) -> int:
    op = await operator_conn()
    try:
        return await VC.leftover_count(op, orgs=FIXTURE_ORGS, users=list(USERS), user_sub_like=f"%{TAG}%", extra=[
            ("SELECT count(*) FROM public.household_memberships WHERE household_id = ANY($1::uuid[])", [[HH_A, HH_B]])])
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
async def y_catalog(conn):
    section("[Y] Constraints read from the catalog before seeding; reserved fixture prefixes match no real row")
    cons = {}
    for t in ("public.household_memberships", "public.accounts", "public.entity_ownership", "public.entity_holdings",
              "public.config", "public.entity_relationships"):
        for r in await platform(conn, "SELECT conname, contype, pg_get_constraintdef(oid) AS d FROM pg_constraint "
                                      "WHERE conrelid = $1::regclass AND contype IN ('p', 'c', 'u', 'f')", t):
            cons[(t, r["conname"])] = r["d"]
    hm = [d for (t, _n), d in cons.items() if t == "public.household_memberships"]
    check(bool("PRIMARY KEY (household_id, entity_id)" in hm and not any(d.startswith("CHECK") for d in hm)),
          "household_memberships: PK (household_id, entity_id), no type CHECK — a legal entity may be a member "
          "(and an entity may sit in several households)", f"{hm}")
    check(bool(any("ownership_pct > (0)" in d for (t, _n), d in cons.items() if t == "public.entity_ownership")),
          "entity_ownership: ownership_pct in (0, 100] (seeded inside it)")
    check(bool(("public.config", "config_org_id_config_key_key") in cons), "config UNIQUE (org_id, config_key)")
    acc_checks = [d for (t, _n), d in cons.items() if t == "public.accounts" and d.startswith("CHECK")]
    find("public.accounts carries no CHECK on registration_type / tax_status — the org's config vocabulary is "
         "enforced in services.household_admin (asserted below by refusal)", f"{acc_checks}")
    reg = await platform(conn, "SELECT count(*) FROM config WHERE category = 'account_registration_types' AND org_id = "
                               "'00000000-0000-0000-0000-000000000001'", fetch="fetchval")
    check(reg == 14, "Part 1 applied: the 14 registration types are config rows of the default org", f"{reg}")
    note_col = await platform(conn, "SELECT count(*) FROM information_schema.columns WHERE table_schema = 'public' "
                                    "AND table_name = 'entity_addresses' AND column_name = 'note'", fetch="fetchval")
    check(note_col == 1, "Part 1 applied: entity_addresses.note exists")
    real = await platform(conn, "SELECT (SELECT count(*) FROM entities WHERE display_name LIKE $1 AND NOT (org_id = "
                                "ANY($4::uuid[]))) + (SELECT count(*) FROM users WHERE auth0_sub LIKE $2 AND NOT (id = "
                                "ANY($5::uuid[]))) + (SELECT count(*) FROM organizations WHERE slug LIKE $3 AND NOT "
                                "(id = ANY($4::uuid[])))",
                          NAME + "%", f"%{TAG}%", f"{TAG}%", FIXTURE_ORGS, list(USERS), fetch="fetchval")
    check(real == 0, "the reserved fixture prefixes match no real row", f"{real}")


async def seed(conn) -> None:
    from services import rbac

    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        for org, nm in ((ORG_A, "a"), (ORG_B, "b")):
            await conn.execute("INSERT INTO organizations (id, name, slug, is_test) VALUES ($1, $2, $3, true)",
                               org, f"PORTENTRY2 Verify Org {nm.upper()}", f"{TAG}-{nm}")
        perm_ids = {r["name"]: r["id"] for r in await conn.fetch("SELECT id, name FROM permissions")}
        missing = sorted(set().union(*(v[4] for v in USERS.values())) - set(perm_ids))
        if missing:
            raise RuntimeError(f"permission catalog lacks {missing}")
        roles = {}
        for uid, (org, _a, _n, rname, perms) in USERS.items():
            if (org, rname) not in roles:
                rid = await rbac.ensure_role(conn, org, rname, f"PORTENTRY2 fixture {rname}")
                for p in perms:
                    await conn.execute("INSERT INTO role_permissions (role_id, permission_id) VALUES ($1, $2) "
                                       "ON CONFLICT DO NOTHING", rid, perm_ids[p])
                roles[(org, rname)] = rid
        for uid, (org, account_role, nm, rname, _p) in USERS.items():
            await conn.execute(
                "INSERT INTO users (id, org_id, email, full_name, auth0_sub, role, status, is_active, is_test) "
                "VALUES ($1, $2, $3, $4, $5, $6, 'active', true, true)",
                uid, org, email_of(uid), f"PORTENTRY2 {nm.title()}", sub_of(uid), account_role)
            await conn.execute("INSERT INTO user_roles (user_id, role_id) VALUES ($1, $2)", uid, roles[(org, rname)])
        for k, (i, org, etype, name, extra) in E.items():
            cols = {"id": i, "org_id": org, "display_name": NAME + name, "status": "active", **extra}
            names = list(cols)
            vals = [f"${n + 1}" for n in range(len(names))]
            await conn.execute(
                f"INSERT INTO entities ({', '.join(names)}, entity_type) VALUES ({', '.join(vals)}, "
                f"${len(names) + 1}::entity_type)", *cols.values(), etype)
        # ownership (entity_relationships): pat → trust 100; trust → llc, llc2, own1 100; lp → child 60; the LP's
        # 7 owners split across BOTH ownership tables (the Olivo Holdings LP shape)
        rel = "INSERT INTO entity_relationships (org_id, from_entity_id, to_entity_id, relationship_type, ownership_pct, " \
              "notes) VALUES ($1, $2, $3, 'ownership', $4, $5)"
        await conn.execute(rel, ORG_A, eid("pat"), eid("trust"), 100, "Grantor / sole beneficiary")
        await conn.execute(rel, ORG_A, eid("trust"), eid("llc"), 100, "Membership interest")
        await conn.execute(rel, ORG_A, eid("trust"), eid("llc2"), 100, "Membership interest")
        await conn.execute(rel, ORG_A, eid("lp"), eid("child"), 60, "Membership interest")
        for i, pct in LP_OWNER_PCTS.items():
            if i in REL_TABLE_OWNERS:
                await conn.execute(rel, ORG_A, eid(f"own{i}"), eid("lp"), pct, "LP units")
            else:
                await conn.execute("INSERT INTO entity_ownership (org_id, parent_id, child_id, ownership_pct) "
                                   "VALUES ($1, $2, $3, $4)", ORG_A, eid(f"own{i}"), eid("lp"), pct)
        # the LP is owned (look-through) by the family: own1 is owned by the trust
        await conn.execute(rel, ORG_A, eid("trust"), eid("own1"), 100, "Membership interest")
        await conn.execute("INSERT INTO households (id, org_id, name) VALUES ($1, $2, $3)", HH_A, ORG_A,
                           NAME + "Family")
        await conn.execute("INSERT INTO households (id, org_id, name) VALUES ($1, $2, $3)", HH_B, ORG_B,
                           NAME + "Org B Family")
        for k in ("pat", "marg"):
            await conn.execute("INSERT INTO household_memberships (household_id, entity_id) VALUES ($1, $2)", HH_A,
                               eid(k))
        await conn.execute("INSERT INTO household_memberships (household_id, entity_id) VALUES ($1, $2)", HH_B,
                           eid("orgb"))
        await conn.execute("INSERT INTO delegate_grants (org_id, principal_entity_id, delegate_user_id, scope) "
                           "VALUES ($1, $2, $3, 'view_only')", ORG_A, eid("pat"), U_MEMBER)
        for k, v in (("pat", "1000.00"), ("trust", "2000.00"), ("llc", "3000.00"), ("lp", "4000.00")):
            await conn.execute("INSERT INTO entity_holdings (org_id, entity_id, taxonomy_key, market_value, "
                               "as_of_date, source) VALUES ($1, $2, 'taxonomy_sc_1', $3::numeric, $4, 'manual')",
                               ORG_A, eid(k), v, SEEDED["as_of"])


SEEDED: dict = {}


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


async def api(uid, method, path, body=None, timeout=120.0):
    import main

    kw = {"headers": {"Authorization": f"Bearer {token_for(uid)}"}}
    if body is not None:
        kw["json"] = body
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app, raise_app_exceptions=False),
                                 base_url="http://verify", timeout=timeout) as client:
        try:
            res = await asyncio.wait_for(client.request(method, path, **kw), timeout)
        except asyncio.TimeoutError:
            return 599, {"timeout": True}
    try:
        parsed = json.loads(res.text) if res.text else {}
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


def node_eval(js: str) -> tuple[int, str]:
    """Run an ES-module snippet in apps/web (the REAL lib files, never a copy); returns (code, stdout)."""
    with tempfile.NamedTemporaryFile("w", suffix=".mjs", dir=str(WEB_DIR / "tests"), delete=False,
                                     encoding="utf-8") as fh:
        fh.write(js)
        path = fh.name
    try:
        code, out, err = run_cmd(["node", path], WEB_DIR, timeout=120)
        return code, (out + ("\n" + err if code else "")).strip()
    finally:
        os.unlink(path)


# ═══ Sections ═══════════════════════════════════════════════════════════════════════════════════════════════════
async def y_role_is_real(conn):
    section("[Y] The verify's DB role is the app role, not a bypass")
    r = await conn.fetchrow("SELECT current_user AS u, rolbypassrls FROM pg_roles WHERE rolname = current_user")
    check(bool(r["rolbypassrls"] is False), "the verify connection does not bypass RLS (rolbypassrls = false)",
          f"{r['u']}")


async def members_of(conn, hh) -> set:
    rows = await scoped(conn, ORG_A, "SELECT entity_id FROM household_memberships WHERE household_id = $1", hh)
    return {r["entity_id"] for r in rows}


async def y_members(conn):
    section("[Y] H-02 / G-02 — legal entities join an EXISTING household; audited; gate both ways")
    before = await members_of(conn, HH_A)
    check(before == {eid("pat"), eid("marg")}, "the fixture household starts with two people", f"{before}")
    body = {"entity_id": str(eid("trust"))}
    c, b = await api(U_SUPPORT, "POST", f"/api/v1/admin/households/{HH_A}/members", body)
    check(bool(c == 403 and "manage_members" in body_text(b)), "support staff: 403 naming manage_members",
          f"{c} {dumps(b)}")
    c, b = await api(U_MEMBER, "POST", f"/api/v1/admin/households/{HH_A}/members", body)
    check(c == 403, "a member: 403", f"{c} {dumps(b)}")
    check(await members_of(conn, HH_A) == before, "the refusals wrote no membership")
    for k in ("trust", "llc", "lp"):
        c, b = await api(U_OA, "POST", f"/api/v1/admin/households/{HH_A}/members", {"entity_id": str(eid(k))})
        check(bool(c == 201 and b.get("entity_type") == E[k][2]),
              f"org admin adds the {E[k][2]} '{E[k][3]}' (identical request shape admitted)", f"{c} {dumps(b)}")
    after = await members_of(conn, HH_A)
    check(after == before | {eid("trust"), eid("llc"), eid("lp")}, "re-read: the trust, the LLC and the LP are "
          "members (independent read, org-scoped RLS)", f"{len(after)}")
    c, b = await api(U_OA, "POST", f"/api/v1/admin/households/{HH_A}/members", {"entity_id": str(eid("trust"))})
    check(c == 409, "the same record again → 409 'already a member'", f"{c} {dumps(b)}")
    c, b = await api(U_OA, "POST", f"/api/v1/admin/households/{HH_A}/members", {"entity_id": str(eid("acctrec"))})
    check(bool(c == 422 and "not a household member" in body_text(b)), "an account record is refused with the reason",
          f"{c} {dumps(b)}")
    c, b = await api(U_OA, "POST", f"/api/v1/admin/households/{HH_A}/members", {"entity_id": str(eid("orgb"))})
    c2, b2 = await api(U_OA, "POST", f"/api/v1/admin/households/{HH_A}/members", {"entity_id": str(MISSING)})
    check(bool(c == 404 and c2 == 404), "another org's record reads exactly like a missing id (404)", f"{c} / {c2}")
    aud = await scoped(conn, ORG_A, "SELECT payload FROM audit_log WHERE org_id = $1 AND action = "
                                    "'household.member_added' AND resource_id = $2", ORG_A, HH_A)
    got = {json.loads(r["payload"])["entity_id"] if isinstance(r["payload"], str) else r["payload"]["entity_id"]
           for r in aud}
    check(got == {str(eid(k)) for k in ("trust", "llc", "lp")}, "each add left a household.member_added audit row",
          f"{got}")
    c, d = await api(U_OA, "GET", f"/api/v1/admin/households/{HH_A}")
    types = sorted({m["entity_type"] for m in d.get("members", [])}) if c == 200 else []
    check(bool(c == 200 and types == ["individual", "llc", "lp", "trust"] and d.get("member_count") == 5),
          "the household detail lists people AND legal entities", f"{c} {types}")
    cand = {x["id"] for x in d.get("owner_candidates", [])} if c == 200 else set()
    check(cand == {str(eid("llc2")), str(eid("own1"))}, "owner candidates = the legal entities a member owns that "
          "are not members (llc2 and own1, both owned by the trust)", f"{cand}")


ACC: dict = {}


async def add_acct(uid, owner, n, **extra):
    body = {"custodian_code": "SCHWAB", "account_number": f"55{n:08d}", "primary_entity_id": str(owner),
            "authority": "read_only", "custody": "outside", "registration_type": "trust", "tax_status": "taxable"}
    body.update(extra)
    return await api(uid, "POST", f"/api/v1/admin/households/{HH_A}/accounts", body)


async def y_accounts(conn):
    section("[Y] H-02 / H-01 — accounts owned by a trust, an LLC, an LP; account fields; DVP required with reason")
    for n, (k, reg) in enumerate((("trust", "trust"), ("llc", "llc"), ("lp", "lp")), start=1):
        c, b = await add_acct(U_OA, eid(k), n, registration_type=reg, is_discretionary=False,
                              service_model="advisory")
        ok = bool(c == 201 and b.get("primary_entity_id") == str(eid(k)))
        check(ok, f"an account owned by the {E[k][2]} is created", f"{c} {dumps(b)}")
        if ok:
            ACC[k] = UUID(b["id"])
    rows = await scoped(conn, ORG_A, "SELECT id, primary_entity_id, registration_type, tax_status, is_billable, "
                                     "is_discretionary, service_model, account_number_masked FROM accounts WHERE "
                                     "household_id = $1 AND system_to IS NULL", HH_A)
    by = {r["id"]: r for r in rows}
    good = all(a in by and by[a]["primary_entity_id"] == eid(k) and by[a]["registration_type"] == k
               and by[a]["tax_status"] == "taxable" and by[a]["is_billable"] is True
               and by[a]["is_discretionary"] is False and by[a]["service_model"] == "advisory"
               and re.fullmatch(r"\*+\d{1,4}", by[a]["account_number_masked"]) for k, a in ACC.items())
    check(bool(len(ACC) == 3 and good), "re-read: owner, registration type, tax status, billable (default on — not a "
          "demo household), discretionary, service model persisted; number stored masked", f"{[dict(r) for r in rows]}")
    c, b = await add_acct(U_OA, eid("pat"), 9, registration_type="brokerage")
    check(bool(c == 422 and "registration type 'brokerage'" in body_text(b)), "a registration type outside the org's "
          "config vocabulary is refused with the list", f"{c} {dumps(b)}")
    c, b = await add_acct(U_SUPPORT, eid("pat"), 10)
    check(bool(c == 403 and "manage_members" in body_text(b)), "support staff cannot add an account (403)", f"{c}")
    # an entity a member owns, not a member: refused without the option, admitted (and linked) with it
    c, b = await add_acct(U_OA, eid("llc2"), 11, registration_type="llc")
    check(bool(c == 422 and "owner must be a member" in body_text(b)), "llc2 (not a member) as owner without 'add as "
          "a member' → 422 with the reason", f"{c} {dumps(b)}")
    c, b = await add_acct(U_OA, eid("llc2"), 11, registration_type="llc", add_owner_as_member=True)
    check(bool(c == 201 and b.get("owner_linked_now") is True), "with 'add as a member' the account is created",
          f"{c} {dumps(b)}")
    check(eid("llc2") in await members_of(conn, HH_A), "… and llc2 is now a member (same transaction)")
    c, b = await add_acct(U_OA, eid("found"), 12, add_owner_as_member=True)
    check(bool(c == 422), "an entity NO member owns cannot ride in on 'add as a member'", f"{c} {dumps(b)}")
    # DVP
    for rec, missing in (({}, "receiving custodian and the DTC / participant number"),
                         ({"custodian_name": "BNY Mellon"}, "DTC / participant number")):
        c, b = await add_acct(U_OA, eid("trust"), 13, custody="other_custodian", authority="advise", receiving=rec)
        check(bool(c == 422 and missing in body_text(b) and "delivery-versus-payment" in body_text(b)),
              f"DVP without {missing.split(' and the ')[-1]} → 422 naming it with the reason", f"{c} {dumps(b)}")
    n_dvp = await scoped(conn, ORG_A, "SELECT count(*) FROM accounts WHERE org_id = $1 AND account_number_masked LIKE "
                                      "'%0013' AND system_to IS NULL", ORG_A, fetch="fetchval")
    check(n_dvp == 0, "the refused DVP accounts wrote nothing", f"{n_dvp}")
    c, b = await add_acct(U_OA, eid("trust"), 13, custody="other_custodian", authority="advise",
                          receiving={"custodian_name": "BNY Mellon", "participant_number": "0901"})
    check(c == 201, "DVP with both receiving fields is accepted", f"{c} {dumps(b)}")
    # edit (system-axis archive, id kept)
    a = ACC.get("trust")
    c, b = await api(U_SUPPORT, "PATCH", f"/api/v1/admin/households/{HH_A}/accounts/{a}", {"tax_status": "tax_exempt"})
    check(bool(c == 403 and "manage_members" in body_text(b)), "support staff cannot edit the account (403)", f"{c}")
    c, b = await api(U_OA, "PATCH", f"/api/v1/admin/households/{HH_A}/accounts/{a}",
                     {"tax_status": "tax_exempt", "is_billable": False})
    check(bool(c == 200 and b.get("tax_status") == "tax_exempt" and b.get("is_billable") is False
               and b.get("id") == str(a)), "org admin edits tax status + billable; the id is kept", f"{c} {dumps(b)}")
    v = await scoped(conn, ORG_A, "SELECT count(*) FILTER (WHERE system_to IS NULL) AS live, count(*) FILTER (WHERE "
                                  "system_to IS NOT NULL AND tax_status = 'taxable') AS archived FROM accounts WHERE "
                                  "org_id = $1 AND account_number_hash = (SELECT account_number_hash FROM accounts "
                                  "WHERE id = $2)", ORG_A, a, fetch="fetchrow")
    live = await scoped(conn, ORG_A, "SELECT tax_status, is_billable FROM accounts WHERE id = $1 AND system_to IS "
                                     "NULL", a, fetch="fetchrow")
    check(bool(v["live"] == 1 and v["archived"] == 1 and live["tax_status"] == "tax_exempt"
               and live["is_billable"] is False), "re-read: one live row (new values) + the prior version archived "
          "on the system axis", f"{dict(v)} {dict(live) if live else None}")
    c, b = await api(U_OA, "PATCH", f"/api/v1/admin/households/{HH_A}/accounts/{a}", {"tax_status": "offshore"})
    check(c == 422, "an edit outside the vocabulary is refused", f"{c} {dumps(b)}")
    # remove: refused while owning an account; a member with none is removed, audited
    c, b = await api(U_OA, "DELETE", f"/api/v1/admin/households/{HH_A}/members/{eid('trust')}")
    check(bool(c == 409 and "owns" in body_text(b)), "removing a member that owns an account → 409 naming the account",
          f"{c} {dumps(b)}")
    c, b = await api(U_SUPPORT, "DELETE", f"/api/v1/admin/households/{HH_A}/members/{eid('marg')}")
    check(c == 403, "support staff cannot remove a member", f"{c}")
    c, b = await api(U_OA, "DELETE", f"/api/v1/admin/households/{HH_A}/members/{eid('marg')}")
    check(c == 200, "org admin removes a member with no account", f"{c} {dumps(b)}")
    check(eid("marg") not in await members_of(conn, HH_A), "re-read: the member is gone")
    aud = await scoped(conn, ORG_A, "SELECT count(*) FROM audit_log WHERE org_id = $1 AND action = "
                                    "'household.member_removed' AND resource_id = $2", ORG_A, HH_A, fetch="fetchval")
    check(aud == 1, "the removal left a household.member_removed audit row", f"{aud}")
    c, d = await api(U_OA, "GET", f"/api/v1/admin/households/{HH_A}")
    cards = d.get("accounts", []) if c == 200 else []
    check(bool(cards and all(x.get("registration_type_label") and x.get("tax_status_label") for x in cards)
               and d.get("account_count") == len(cards) == 5), "the account cards carry type / tax labels (from "
          "config); account_count is live", f"{c} {dumps(cards)}")
    olivo = await platform(conn, "SELECT id FROM households WHERE name = 'Olivo Family'", fetch="fetchval")
    if olivo is None:
        find("no 'Olivo Family' household in this database — the demo billable default is proven by the node test only")
    else:
        demo = await platform(conn, "SELECT public.crm_is_demo_household($1)", olivo, fetch="fetchval")
        check(demo is True, "the Olivo household is a demo household → Add account defaults billable OFF there "
              "(services.household_admin.add_account: is_billable None → not crm_is_demo_household)", f"{demo}")


async def y_readers(conn):
    section("[Y] Every reader handles entity members / entity owners")
    c, d = await api(U_OA, "GET", f"/api/v1/admin/households/{HH_A}")
    gross = Decimal(str((d.get("net_worth") or {}).get("gross") or "0")) if c == 200 else None
    want = Decimal("10000.00")  # pat 1000 + trust 2000 + llc 3000 + lp 4000
    check(gross == want, "net worth gross includes the entity members' holdings (person + trust + LLC + LP)",
          f"{gross} vs {want}; {dumps(d.get('net_worth'))}")
    c, r = await api(U_OA, "GET", f"/api/v1/admin/households/{HH_A}/rollup")
    check(bool(c == 200 and Decimal(str(r.get("total_holdings_value"))) == want and r.get("member_count") == 5),
          "the flexible rollup sums every member, entity members included", f"{c} {dumps(r)}")
    # the member visibility closure, through the API the member actually calls (member_entity_scope → the delegate
    # grant's ownership subtree): the trust and the LLCs that own accounts are inside it
    c, b = await api(U_MEMBER, "GET", "/api/v1/entities/search?q=PE2V&page_size=100")
    vis = {i["id"] for i in b.get("items", [])} if c == 200 else set()
    check(bool(c == 200 and {str(eid(k)) for k in ("trust", "llc", "llc2")} <= vis and str(eid("found")) not in vis),
          "the member visibility closure reaches the trust and the LLCs that own accounts (and not the unrelated "
          "foundation)", f"{c} {len(vis)}")
    # the X-ray household rule (routers.xray._visible_households): a household is the member's when one of its live
    # accounts is owned by an entity in that closure — evaluated in SQL on the same closure
    hh = await scoped(conn, ORG_A, "SELECT DISTINCT household_id FROM accounts WHERE org_id = $1 AND household_id IS "
                                   "NOT NULL AND primary_entity_id = ANY($2::uuid[]) AND primary_entity_id <> $3 AND "
                                   "system_to IS NULL AND valid_to IS NULL", ORG_A, [UUID(x) for x in vis], eid("pat"))
    check(HH_A in {r["household_id"] for r in hh}, "the X-ray household rule admits the member's family through "
          "ENTITY-owned accounts alone (the person owns none)", f"{[r['household_id'] for r in hh]}")
    a = ACC.get("llc")
    c, b = await api(U_OA, "GET", f"/api/v1/held-away/accounts/{a}")
    check(c == 200, "held-away entry opens an account owned by an LLC", f"{c} {dumps(b)}")
    find("billing groups key on entities.primary_household_id (strict, non-overlapping) — membership changes here do "
         "not move billing; an entity owner is billed through its own primary household as before")


async def y_permissions(conn):
    section("[Y] PERS-01 — permission-true controls (envelope) AND the server refusal on the identical request")
    c, s_env = await api(U_SUPPORT, "GET", f"/api/v1/entities/{eid('lp')}/ownership")
    c2, a_env = await api(U_OA, "GET", f"/api/v1/entities/{eid('lp')}/ownership")
    check(bool(c == 200 and s_env.get("permissions", {}).get("can_write") is False
               and s_env.get("vocabularies", {}).get("editable") == []),
          "support staff's ownership envelope: can_write false, editable []", f"{c} {dumps(s_env.get('permissions'))}")
    check(bool(c2 == 200 and a_env.get("permissions", {}).get("can_write") is True), "the org admin's: can_write true",
          f"{c2}")
    c, sp = await api(U_SUPPORT, "GET", f"/api/v1/entities/{eid('lp')}/permissions")
    c2, ap = await api(U_OA, "GET", f"/api/v1/entities/{eid('lp')}/permissions")
    s_can = (sp.get("permissions") or {}).get("can") or {}
    a_can = (ap.get("permissions") or {}).get("can") or {}
    check(bool(c == 200 and not any(s_can.get(k) for k in ("ownership", "record", "tax_ids", "relationships",
                                                             "details", "accounts"))
               and s_can.get("addresses") is True and s_can.get("notes") is True),
          "support staff's record envelope: no ownership / record / tax ids / relationships / details / accounts; "
          "addresses + notes yes (edit_crm_contact)", f"{c} {s_can}")
    check(bool(c2 == 200 and all(a_can.get(k) for k in ("ownership", "record", "tax_ids", "relationships",
                                                          "details", "accounts"))), "the org admin's: all of them",
          f"{c2} {a_can}")
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as fh:
        json.dump({"support_own": s_env, "admin_own": a_env, "support": sp, "admin": ap}, fh)
        env_path = fh.name
    try:
        code, out = node_eval(
            "import { readFileSync } from 'node:fs';\n"
            "const P = await import('../lib/portentry2.mjs');\n"
            f"const e = JSON.parse(readFileSync({json.dumps(env_path)}, 'utf8'));\n"
            "const keys = ['ownership', 'record', 'tax_ids', 'relationships', 'details', 'accounts'];\n"
            "console.log(JSON.stringify({ so: P.canEditOwnership(e.support_own), ao: P.canEditOwnership(e.admin_own),"
            " s: keys.map((k) => P.canEdit(e.support, k)), a: keys.map((k) => P.canEdit(e.admin, k)) }));\n")
    finally:
        os.unlink(env_path)
    try:
        r = json.loads(out.splitlines()[-1])
    except (ValueError, IndexError):
        r = {}
    check(bool(code == 0 and r.get("so") is False and not any(r.get("s", [True])) and r.get("ao") is True
               and all(r.get("a", [False]))), "the REAL envelopes fed to the page's own render rule: support staff "
          "renders no ownership / record control, the org admin does", out[-300:])
    # server refusal on the identical request
    rel = await scoped(conn, ORG_A, "SELECT id, ownership_pct FROM entity_relationships WHERE to_entity_id = $1 AND "
                                    "from_entity_id = $2 AND relationship_type = 'ownership' AND valid_to IS NULL",
                       eid("lp"), eid("own3"), fetch="fetchrow")
    body = {"ownership_pct": 11.0, "effective_date": None, "note": "LP units", "change_reason": "correction"}
    c, b = await api(U_SUPPORT, "PATCH", f"/api/v1/entity-relationships/{rel['id']}/ownership", body)
    still = await scoped(conn, ORG_A, "SELECT ownership_pct FROM entity_relationships WHERE id = $1", rel["id"],
                         fetch="fetchval")
    check(bool(c == 403 and "manage_members" in body_text(b) and Decimal(still) == Decimal(rel["ownership_pct"])),
          "support staff's ownership-% save → 403 naming manage_members; the row is unchanged", f"{c} {dumps(b)}")
    c, b = await api(U_OA, "PATCH", f"/api/v1/entity-relationships/{rel['id']}/ownership", body)
    check(c == 200, "the identical request is admitted for the org admin", f"{c} {dumps(b)}")
    # put it back the documented way (a second correction) so the LP keeps its 100% for the views section
    cur = await scoped(conn, ORG_A, "SELECT id FROM entity_relationships WHERE to_entity_id = $1 AND from_entity_id = "
                                    "$2 AND relationship_type = 'ownership' AND valid_to IS NULL AND system_to IS NULL",
                       eid("lp"), eid("own3"), fetch="fetchval")
    c, b = await api(U_OA, "PATCH", f"/api/v1/entity-relationships/{cur}/ownership",
                     {**body, "ownership_pct": 10.0})
    check(c == 200, "restored to 10% through the same route", f"{c} {dumps(b)}")


async def y_menu(conn):
    section("[Y] PERS-03 — the menu shows support staff what its grants open")
    c, me = await api(U_SUPPORT, "GET", "/api/v1/users/me")
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as fh:
        json.dump(me, fh)
        path = fh.name
    try:
        code, out = node_eval(
            "import { readFileSync } from 'node:fs';\n"
            "const M = await import('../lib/menuVisibility.mjs');\n"
            f"const me = JSON.parse(readFileSync({json.dumps(path)}, 'utf8'));\n"
            "console.log(JSON.stringify(M.visibleMenuItems(me).map((i) => i.href)));\n")
    finally:
        os.unlink(path)
    try:
        hrefs = json.loads(out.splitlines()[-1])
    except (ValueError, IndexError):
        hrefs = []
    check(bool(code == 0 and "/portfolio/households" not in hrefs and "/admin/workflows" not in hrefs
               and "/admin/workflows/runs" in hrefs), "support staff: no Households, no workflow library "
          "(author_workflows); Runs shown (view_workflow_runs)", f"{c} {out[-300:]}")
    for path, label in (("/api/v1/admin/households", "Households"),):
        c, _ = await api(U_SUPPORT, "GET", path)
        check(c == 403, f"hidden ⇔ refused: {label} API → 403 for support staff", f"{c}")
    c, _ = await api(U_OA, "GET", "/api/v1/admin/households")
    check(c == 200, "… and 200 for the org admin (the nav row shows for it)", f"{c}")


async def y_views(conn):
    section("[Y] F-01 / F-02 / F-03 — the ownership views agree; direct vs look-through; 60.0%; notes")
    lp = eid("lp")
    owners = {str(eid(f"own{i}")) for i in LP_OWNER_PCTS}
    c, g = await api(U_OA, "GET", f"/api/v1/entities/{lp}/ownership-graph")
    g_owners = [e["parent_id"] for e in g.get("edges", []) if e["child_id"] == str(lp)] if c == 200 else []
    c2, t = await api(U_OA, "GET", f"/api/v1/entities/{lp}/tree")
    t_owners = [o["id"] for o in (t.get("tree") or t).get("owners", [])] if c2 == 200 else []
    c3, o = await api(U_OA, "GET", f"/api/v1/entities/{lp}/ownership")
    o_owners = [i["counterparty"]["id"] for i in o.get("owned_by", [])] if c3 == 200 else []
    check(bool(set(g_owners) == set(t_owners) == set(o_owners) == owners and len(g_owners) == len(t_owners)
               == len(o_owners) == 7), "the Overview / tab tree (ownership-graph), the hierarchy graph (/tree) and "
          "OWNED BY list agree on the LP's 7 owners (5 in entity_relationships, 2 in entity_ownership), no duplicate",
          f"{len(g_owners)}/{len(t_owners)}/{len(o_owners)}")
    total = o.get("owned_by_total_pct") if c3 == 200 else None
    check(bool(total is not None and abs(total - 100.0) < 1e-6), "the owners total 100%", f"{total}")
    notes = {i["counterparty"]["id"]: i.get("notes") for i in o.get("owned_by", [])}
    check(all(notes.get(str(eid(f"own{i}"))) == "LP units" for i in REL_TABLE_OWNERS), "F-01: each OWNED BY row "
          "carries its interest note", f"{notes}")
    c, lt = await api(U_OA, "GET", f"/api/v1/entities/{lp}/lookthrough")
    items = lt.get("lookthrough", lt if isinstance(lt, list) else []) if c == 200 else []
    raw = next((x.get("effective_pct") for x in items if x.get("entity_id") == str(eid("child"))), None)
    check(raw is not None and abs(float(raw) - 0.6) < 1e-9, "1c reproduced: /lookthrough gives the 60% holding as the "
          "FRACTION 0.6", f"{raw}")
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as fh:
        json.dump({"lt": items, "graph": g, "root": str(eid("trust"))}, fh)
        path = fh.name
    try:
        code, out = node_eval(
            "import { readFileSync } from 'node:fs';\n"
            "const P = await import('../lib/portentry2.mjs');\n"
            f"const d = JSON.parse(readFileSync({json.dumps(path)}, 'utf8'));\n"
            f"const m = P.lookthroughPercentMap(d.lt);\n"
            f"console.log(JSON.stringify({{ pct: P.formatPct(m[{json.dumps(str(eid('child')))}]),"
            " old: `${Number(d.lt.find((x) => x.entity_id === " + json.dumps(str(eid("child"))) + ").effective_pct)"
            ".toFixed(1)}%` }));\n")
    finally:
        os.unlink(path)
    try:
        r = json.loads(out.splitlines()[-1])
    except (ValueError, IndexError):
        r = {}
    check(bool(code == 0 and r.get("pct") == "60.0%" and r.get("old") == "0.6%"), "F-03: the graph's formatter "
          "prints 60.0% (the old path printed 0.6%)", out[-200:])
    # direct vs look-through on the trust: it owns llc, llc2, own1 directly; own1 → lp → child below
    c, tg = await api(U_OA, "GET", f"/api/v1/entities/{eid('trust')}/ownership-graph")
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as fh:
        json.dump(tg, fh)
        path = fh.name
    try:
        code, out = node_eval(
            "import { readFileSync } from 'node:fs';\n"
            "const P = await import('../lib/portentry2.mjs');\n"
            f"const g = JSON.parse(readFileSync({json.dumps(path)}, 'utf8'));\n"
            f"const root = {json.dumps(str(eid('trust')))};\n"
            "const d = P.ownsRows(g, root).map((r) => r.id); const l = P.ownsRows(g, root, { lookThrough: true });\n"
            "console.log(JSON.stringify({ d, l: l.map((r) => r.id), dup: l.length - new Set(l.map((r) => r.id)).size }));\n")
    finally:
        os.unlink(path)
    try:
        r = json.loads(out.splitlines()[-1])
    except (ValueError, IndexError):
        r = {}
    direct = {str(eid(k)) for k in ("llc", "llc2", "own1")}
    check(bool(code == 0 and set(r.get("d", [])) == direct and len(r.get("d", [])) == 3),
          "'Owns' by default: the trust's 3 DIRECT holdings only", out[-300:])
    check(bool(code == 0 and str(eid("lp")) in r.get("l", []) and str(eid("child")) in r.get("l", [])
               and r.get("dup") == 0), "look-through toggle adds the LP and its holding, no duplicate row", out[-300:])


async def y_picker_dates_details(conn):
    section("[Y] Picker (tokens, deceased), dates, detail keys, address note, seasonal current")
    q = "PE2V Marguerite Olivo"
    contiguous = await scoped(conn, ORG_A, "SELECT count(*) FROM entities WHERE org_id = $1 AND (lower(display_name) "
                                           "LIKE $2 OR lower(coalesce(legal_name, '')) LIKE $2)", ORG_A,
                              f"%{q.lower()}%", fetch="fetchval")
    check(contiguous == 0, "D19-01 reproduced: the old contiguous-substring match finds nothing for "
          "'Marguerite Olivo'", f"{contiguous}")
    c, b = await api(U_OA, "GET", f"/api/v1/entities/search?q={q.replace(' ', '%20')}")
    names = [i["display_name"] for i in b.get("items", [])] if c == 200 else []
    check(names == [NAME + "Marguerite Meloni Olivo"], "token search finds Marguerite Meloni Olivo (and nothing "
          "else)", f"{c} {names}")
    c, b = await api(U_OA, "GET", "/api/v1/entities/search?q=PE2V%20Theresa")
    off = [i["display_name"] for i in b.get("items", [])] if c == 200 else ["?"]
    c2, b2 = await api(U_OA, "GET", "/api/v1/entities/search?q=PE2V%20Theresa&include_inactive=true")
    on = [i["display_name"] for i in b2.get("items", [])] if c2 == 200 else []
    check(bool(off == [] and on == [NAME + "Theresa Zucchini Olivo"]), "E4-01: the deceased record is found only "
          "with 'include inactive / deceased' (ON by default for family types — node test)", f"{off} / {on}")
    # dates through the real API + the page's parser
    c, b = await api(U_OA, "PUT", f"/api/v1/entities/{eid('pat')}", {"date_of_death": "275760-01-01"})
    check(c == 422, "a mangled year (275760) is refused by the server too", f"{c} {dumps(b)}")
    code, out = node_eval(
        "const P = await import('../lib/portentry2.mjs');\n"
        "console.log(JSON.stringify(['1951-03-07', '03/07/1951', ' 1951/3/7 ', '275760-01-01', '1799-12-31', "
        "'01/01/51'].map((s) => P.parseDateInput(s).iso)));\n")
    try:
        r = json.loads(out.splitlines()[-1])
    except (ValueError, IndexError):
        r = []
    check(r == ["1951-03-07", "1951-03-07", "1951-03-07", "", "", ""], "typed / pasted ISO and US dates round-trip; "
          "out-of-range and two-digit years are refused", out[-200:])
    c, b = await api(U_OA, "PUT", f"/api/v1/entities/{eid('marg')}", {"inception_date": r[0] if r else "1951-03-07"})
    got = await scoped(conn, ORG_A, "SELECT inception_date FROM entities WHERE id = $1 AND system_to IS NULL",
                       eid("marg"), fetch="fetchval")
    check(bool(c == 200 and got == date(1951, 3, 7)), "the parsed date saves and re-reads", f"{c} {got}")
    # detail keys
    vals = {"trust_type": "grat", "remainder_to": str(eid("found")),
            "shares": "1/3 each to the three children, per stirpes", "beneficiaries_note": "Spouse, then descendants"}
    c, b = await api(U_SUPPORT, "PUT", f"/api/v1/entities/{eid('grat')}/details", {"values": vals})
    check(c == 403, "support staff cannot save detail fields", f"{c}")
    c, b = await api(U_OA, "PUT", f"/api/v1/entities/{eid('grat')}/details", {"values": vals})
    check(c == 200, "remainder_to / shares / beneficiaries_note save", f"{c} {dumps(b)}")
    c, d = await api(U_OA, "GET", f"/api/v1/entities/{eid('grat')}/details")
    fields = {f["key"]: f for f in (d.get("rows") or {}).get("fields", [])} if c == 200 else {}
    check(bool(fields.get("remainder_to", {}).get("display") == NAME + "Family Foundation"
               and fields.get("shares", {}).get("display") == vals["shares"]
               and fields.get("beneficiaries_note", {}).get("display") == vals["beneficiaries_note"]),
          "re-read: the remainder shows the CRM record's NAME; the two texts round-trip",
          f"{ {k: fields.get(k, {}).get('display') for k in ('remainder_to', 'shares', 'beneficiaries_note')} }")
    # address note + seasonal current
    addr = {"address_type": "primary_residence", "street1": "1 Ocean Blvd", "city": "Palm Beach", "country": "US",
            "is_seasonal": True, "season_from_month": 11, "season_to_month": 4, "note": "Winter residence"}
    c, a = await api(U_OA, "POST", f"/api/v1/entities/{eid('pat')}/addresses", addr)
    check(bool(c == 201 and a.get("note") == "Winter residence"), "C-01: an address saves with its note", f"{c} {dumps(a)}")
    c, full = await api(U_OA, "GET", f"/api/v1/entities/{eid('pat')}/full")
    notes = [x.get("note") for x in full.get("addresses", [])] if c == 200 else []
    check("Winter residence" in notes, "… and re-reads with it", f"{notes}")
    code, out = node_eval(
        "const P = await import('../lib/portentry2.mjs');\n"
        "const a = { is_seasonal: true, season_from_month: 11, season_to_month: 4 };\n"
        "const b = { is_seasonal: true, season_from_month: 5, season_to_month: 10 };\n"
        "console.log(JSON.stringify([P.seasonalStatus(a, '2026-01-15').label, P.seasonalStatus(b, '2026-01-15').label,"
        " P.seasonalStatus(a, '2026-07-04').label]));\n")
    check(out.endswith('["Current · Nov–Apr","Seasonal, May–Oct","Seasonal, Nov–Apr"]'), "seasonal 'current' is "
          "right for fixture dates (Jan → the Nov–Apr address; Jul → not)", out[-200:])


async def y_security(conn):
    section("[Y] P.1 — staff security search; a member refused; the master admin screen stays Super Admin")
    for q, label in (("AAPL", "ticker"), ("Apple", "name")):
        c, b = await api(U_SUPPORT, "GET", f"/api/v1/securities/search?q={q}")
        rows = b.get("rows", []) if c == 200 else []
        hit = next((r for r in rows if (r.get("identifiers") or {}).get("ticker") == "AAPL"), None)
        if c == 200 and not rows:
            find(f"the security master returned nothing for {q!r} — is the master loaded in this database?")
        check(bool(c == 200 and hit is not None and (label != "ticker" or rows[0] is hit)),
              f"support staff finds AAPL by {label}" + (" (first)" if label == "ticker" else ""),
              f"{c} {dumps(rows[:2])}")
    c, b = await api(U_MEMBER, "GET", "/api/v1/securities/search?q=AAPL")
    check(c == 403, "a member is refused", f"{c} {dumps(b)}")
    c, b = await api(U_OA, "GET", "/api/v1/admin/security-data/master/status")
    check(c == 403, "the master admin screen's API stays Super Admin (org admin → 403)", f"{c}")


async def y_isolation(conn):
    section("[Y] Org isolation both directions (rolbypassrls = false)")
    r = await conn.fetchrow("SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user")
    check(bool(r["rolbypassrls"] is False), "isolation is measured on a non-bypassing role")
    a = await scoped(conn, ORG_A, "SELECT count(*) FROM household_memberships WHERE household_id = $1", HH_A,
                     fetch="fetchval")
    b = await scoped(conn, ORG_B, "SELECT count(*) FROM household_memberships WHERE household_id = $1", HH_A,
                     fetch="fetchval")
    a2 = await scoped(conn, ORG_A, "SELECT count(*) FROM household_memberships WHERE household_id = $1", HH_B,
                      fetch="fetchval")
    b2 = await scoped(conn, ORG_B, "SELECT count(*) FROM household_memberships WHERE household_id = $1", HH_B,
                      fetch="fetchval")
    check(bool(a > 0 and b == 0 and b2 > 0 and a2 == 0), "RLS household_memberships: each org reads its own, none "
          "of the other's", f"A:{a}/{a2} B:{b}/{b2}")
    for method, path, body in (("POST", f"/api/v1/admin/households/{HH_A}/members", {"entity_id": str(eid("orgb"))}),
                               ("GET", f"/api/v1/admin/households/{HH_A}", None),
                               ("GET", f"/api/v1/entities/{eid('lp')}/permissions", None),
                               ("PATCH", f"/api/v1/admin/households/{HH_A}/accounts/{ACC.get('llc', MISSING)}",
                                {"tax_status": "taxable"})):
        c, bb = await api(U_OB_ADMIN, method, path, body)
        check(c == 404, f"org B's admin on org A's {path.split('/')[-1] or path} → 404", f"{c} {dumps(bb)}")
    c, bb = await api(U_OA, "GET", f"/api/v1/admin/households/{HH_B}")
    check(c == 404, "org A's admin on org B's household → 404", f"{c}")


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
    m2 = re.search(r"^# pass (\d+)", out, re.M)
    code2, out2, _ = run_cmd(["node", "--test", "tests/portentry2/"], WEB_DIR, timeout=300)
    check(bool(code2 == 0 and re.search(r"^# fail 0", out2, re.M)), "the sprint's node tests (date input, picker, "
          "permission rendering, ownership views, households, nav) pass", f"total pass {m2.group(1) if m2 else '?'}")
    code, out, err = run_cmd(["npm", "run", "build"], WEB_DIR, timeout=1800)
    check(bool(code == 0), "npm run build exits 0", (out + err)[-400:] if code else "")


def regression():
    section("[Y] Regression — verify_portentry1.py ONCE, flat, AFTER teardown, VERIFY_NESTED=1, 2700 s")
    env = os.environ | {"VERIFY_NESTED": "1"}
    env.pop("PORTFOLIO_ROLLUP_DEBOUNCE_S", None)
    code, out, err = run_cmd([sys.executable, str(HERE / "verify_portentry1.py")], REPO, timeout=2700, env=env)
    m = re.search(r"TOTAL: (\d+) PASS, (\d+) FAIL", out)
    fails = [ln for ln in out.splitlines() if ln.startswith("[FAIL]")]
    check(bool(m is not None and m.group(2) == "0" and code == 0), "verify_portentry1.py: 0 FAIL",
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
    pre = real = demo = None
    try:
        await run("pre-teardown", teardown, conn)
        pre = await counts(conn)
        real = await real_prints(conn)
        demo = await demo_prints(conn)
        find("demo-olivo rows fingerprinted", f"{ {k: v[0] for k, v in demo.items()} }")
        SEEDED["as_of"] = date.today()
        await run("role", y_role_is_real, conn)
        await run("catalog", y_catalog, conn)
        await run("seed", seed, conn)
        await run("fixtures", y_fixtures, conn)
        section("[Y] Task 1 — findings 1a–1g")
        self_task1(check, find)
        await run("members", y_members, conn)
        await run("accounts", y_accounts, conn)
        await run("readers", y_readers, conn)
        await run("permissions", y_permissions, conn)
        await run("menu", y_menu, conn)
        await run("ownership views", y_views, conn)
        await run("picker / dates / details", y_picker_dates_details, conn)
        await run("security search", y_security, conn)
        await run("isolation", y_isolation, conn)
        section("[Y] Static — schema file, docs, regression set, front-end markers")
        await run("static", self_static, check, find)
        await run("routes", y_routes, conn)
        await run("front end", y_front_end, conn)
        check(not NETWORK_CALLS, "no network call was attempted", f"{NETWORK_CALLS}")
    finally:
        section("[Y] Teardown — zero fixture rows; every counted table at its pre-test count; demo + real rows unchanged")
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
                now = await real_prints(conn)
                for t in REAL_FINGERPRINTED:
                    check(now[t] == real[t], f"real {t} unchanged (count + checksum by primary key)",
                          f"{real[t][0]} → {now[t][0]} rows")
            if demo is not None:
                now = await demo_prints(conn)
                for k in DEMO_SQL:
                    check(now[k] == demo[k], f"demo-olivo {k} unchanged (count + checksum)",
                          f"{demo[k][0]} → {now[k][0]}")
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
