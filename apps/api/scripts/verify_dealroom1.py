"""verify_dealroom1 — Marketplace Deal Room, Phase 1 (DEALROOM1).

WRITTEN, NOT RUN by the sprint. The operator runs:

    doppler run -- apps/api/venv/bin/python apps/api/scripts/verify_dealroom1.py 2>&1 | grep -E "^\\[FAIL\\]|^TOTAL"

Pass/fail only, no prompts. Prints 'TOTAL: N PASS, M FAIL' and exits non-zero
on any failure. AUTHZ_TEST_MODE=1 is set before anything imports ``main``;
secrets are hydrated from Doppler over HTTPS (``_db_connect``) first. R2 is
mocked: upload / delete are recorders, ``get_signed_url`` returns a fake URL,
and ``get_s3_client`` fails the run if anything reaches real R2.

Everything after the bearer token is the real app (middleware, Principal,
every gate, every handler, RLS) against the live database. The script's own
connection is DATABASE_URL = app_service (rolbypassrls = false, asserted).

FIXTURES: two fixture orgs (99000000-…-0000000d0101 / d0102). Every fixture
user is created explicitly with the auth0_sub its token carries and an
@test.local email (users.is_test = true by trigger), so every signature a
fixture makes lands in the 'test' chain, never the real 'platform' chain.

TEARDOWN (start and end): deal_signatures is append-only — no RLS UPDATE /
DELETE policy and a trigger that refuses both. The ONLY removal path is the
privileged one documented in docs/dealroom1_part1.sql: a non-app_service
session (the operator DSN DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP, role
postgres, which bypasses RLS) that sets dealroom.signature_teardown =
'fixture' inside its transaction, and only for 'test'-chain rows. app_service
cannot use it (no policy → zero rows; proven below). Everything else is
removed FK child-before-parent (pg_constraint, recursive), then every counted
table is re-read for its exact pre-test count; real note_gold_values must be
unchanged (count + checksum).

With VERIFY_NESTED=1 the regression section prints one '[SKIP] nested run'.
"""

from __future__ import annotations

import os as _authz_os
_authz_os.environ["AUTHZ_TEST_MODE"] = "1"  # AUTHZ1: set before main is imported

import asyncio
import hashlib
import json
import os
import pathlib
import re
import subprocess
import sys
import traceback
from uuid import UUID

HERE = pathlib.Path(__file__).resolve().parent
API_DIR = HERE.parent
REPO = API_DIR.parents[1]
WEB_DIR = REPO / "apps" / "web"
sys.path.insert(0, str(HERE))

from _db_connect import admin_dsn, connect, hydrate, resolve_dsn  # noqa: E402

hydrate()  # BEFORE main is imported (Settings is lru_cached)

import httpx  # noqa: E402

import _authz1_common as C  # noqa: E402

BEFORE_REF = "b07c839"   # dealroom1-work base — the pre-sprint code
TAG = "dealroom1verify"
OPERATOR_DSN_VAR = "DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP"

ORG_A = UUID("99000000-0000-0000-0000-0000000d0101")
ORG_B = UUID("99000000-0000-0000-0000-0000000d0102")
FIXTURE_ORGS = [ORG_A, ORG_B]


def _id(n: int) -> UUID:
    return UUID(f"99000000-0000-0000-0000-0000000d0{n:03d}")


# users: (org, users.role, short name, rbac role, full name)
U_SUPER, U_STAFF, U_ORGADMIN, U_PUB, U_DELEG, U_OTHERPUB, U_MEMBER, U_UNACC, U_KYC, U_NAMED, U_APPROVED, \
    U_OTHERORG = (_id(110 + i) for i in range(12))
USERS = {
    U_SUPER: (ORG_A, "super_admin", "super", "super_admin", "DR1 Super Admin"),
    U_STAFF: (ORG_A, "member", "staff", "investment_staff", "DR1 Staff"),
    U_ORGADMIN: (ORG_A, "org_admin", "orgadmin", None, "DR1 Org Admin"),
    U_PUB: (ORG_A, "member", "pub", "member", "DR1 Publisher"),
    U_DELEG: (ORG_A, "member", "deleg", "member", "DR1 Delegate"),
    U_OTHERPUB: (ORG_A, "member", "otherpub", "member", "DR1 Other Publisher"),
    U_MEMBER: (ORG_A, "member", "member", "member", "Jane Accredited"),
    U_UNACC: (ORG_A, "member", "unacc", "member", "Una Unaccredited"),
    U_KYC: (ORG_A, "member", "kyc", "member", "Kim Pendingkyc"),
    U_NAMED: (ORG_A, "member", "named", "member", "Nate Named"),
    U_APPROVED: (ORG_A, "member", "approved", "member", "Abe Approved"),
    U_OTHERORG: (ORG_B, "member", "otherorg", "member", "Olga Otherorg"),
}
# entities
E_PUB, E_OTHERPUB, E_MEM, E_UNACC, E_KYC, E_NAMED, E_APPROVED, E_B = (_id(130 + i) for i in range(8))
INVESTOR_ENTITY = {U_MEMBER: E_MEM, U_UNACC: E_UNACC, U_KYC: E_KYC, U_NAMED: E_NAMED, U_APPROVED: E_APPROVED,
                   U_OTHERORG: E_B}
D_B = _id(150)   # org B deal (seeded directly)

COUNTED = (
    "public.users", "public.organizations", "public.deals", "public.deal_documents", "public.deal_folders",
    "public.deal_folder_named", "public.deal_delegates", "public.deal_room_settings", "public.deal_agreements",
    "public.deal_signatures", "public.deal_access", "public.entity_publishers", "public.entity_publisher_users",
    "public.agreement_templates", "public.entities", "public.entity_addresses", "public.compliance_records",
    "public.delegate_grants", "public.member_todos", "public.audit_log", "public.config", "public.notifications",
    "public.roles", "public.user_roles",
)

S: dict = {}      # state shared between sections (deal ids, folder ids, doc ids …)
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


def git_show(ref: str, path: str) -> str:
    code, out, _ = run_cmd(["git", "show", f"{ref}:{path}"], REPO, timeout=60)
    return out if code == 0 else ""


def sub_of(uid: UUID) -> str:
    return f"auth0|{TAG}-{USERS[uid][2]}"


def email_of(uid: UUID) -> str:
    return f"{TAG}-{USERS[uid][2]}@test.local"


def dumps(x) -> str:
    return json.dumps(x, ensure_ascii=False, default=str)[:400]


# ═══ DB helpers (verify's own app_service connection; RLS context explicit) ═══
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


async def gold_fingerprint(conn) -> tuple[int, str]:
    row = await platform(conn, "SELECT count(*) AS n, md5(COALESCE(string_agg(t::text, '' ORDER BY t::text), '')) "
                               "AS s FROM portfolio.note_gold_values t", fetch="fetchrow")
    return int(row["n"]), row["s"]


_FK_CACHE: dict = {}


async def fk_children(conn, table: str):
    if table not in _FK_CACHE:
        _FK_CACHE[table] = await conn.fetch("""
            SELECT cn.nspname || '.' || cc.relname AS child,
                   array_agg(ca.attname ORDER BY k.ord) AS child_cols,
                   array_agg(pa.attname ORDER BY k.ord) AS parent_cols
            FROM pg_constraint c
            JOIN LATERAL unnest(c.conkey, c.confkey) WITH ORDINALITY AS k(ck, pk, ord) ON true
            JOIN pg_class cc ON cc.oid = c.conrelid JOIN pg_namespace cn ON cn.oid = cc.relnamespace
            JOIN pg_attribute ca ON ca.attrelid = c.conrelid AND ca.attnum = k.ck
            JOIN pg_attribute pa ON pa.attrelid = c.confrelid AND pa.attnum = k.pk
            WHERE c.contype::text = 'f' AND c.confrelid = $1::regclass
            GROUP BY c.oid, cn.nspname, cc.relname""", table)
    return _FK_CACHE[table]


async def delete_tree(conn, table: str, where: str, params: list, depth=0, log=None, path=()):
    """Child-before-parent delete, FKs read from pg_constraint (recursively)."""
    if depth > 14:
        raise RuntimeError(f"FK recursion too deep at {table} via {' > '.join(path)}")
    path = path + (table,)
    for fk in await fk_children(conn, table):
        if fk["child"] in path:
            continue
        cc = ", ".join(f'"{c}"' for c in fk["child_cols"])
        pc = ", ".join(f'"{c}"' for c in fk["parent_cols"])
        await delete_tree(conn, fk["child"], f"({cc}) IN (SELECT {pc} FROM {table} WHERE {where})",
                          params, depth + 1, log, path)
    status = await conn.execute(f"DELETE FROM {table} WHERE {where}", *params)
    n = int(status.rsplit(" ", 1)[-1])
    if n and log is not None:
        log.append(f"{table}: {n}")


async def operator_conn():
    dsn, how = await resolve_dsn(OPERATOR_DSN_VAR)
    if not dsn:
        raise RuntimeError(f"no operator DSN for the signature teardown path: {how}")
    return await connect(dsn)


async def purge_fixture_signatures() -> int:
    """THE privileged teardown path (see module docstring). Test chain only."""
    op = await operator_conn()
    try:
        async with op.transaction():
            await op.execute("SELECT set_config('dealroom.signature_teardown', 'fixture', true)")
            status = await op.execute(
                "DELETE FROM deal_signatures WHERE org_id = ANY($1::uuid[]) AND chain = 'test'", FIXTURE_ORGS)
        return int(status.rsplit(" ", 1)[-1])
    finally:
        await op.close()


async def teardown(conn) -> list[str]:
    log: list[str] = []
    n = await purge_fixture_signatures()
    if n:
        log.append(f"deal_signatures (privileged path): {n}")
    users = list(USERS)
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        real_refs = await conn.fetchval(
            "SELECT count(*) FROM deals WHERE NOT (org_id = ANY($1::uuid[])) AND (created_by = ANY($2::uuid[]) "
            "OR submitted_by = ANY($2::uuid[]) OR reviewed_by = ANY($2::uuid[]))", FIXTURE_ORGS, users)
        if real_refs:
            raise RuntimeError(f"{real_refs} real deal(s) reference a fixture user — refusing to delete it")
        await conn.execute("DELETE FROM audit_log WHERE org_id = ANY($1::uuid[]) OR user_id = ANY($2::uuid[])",
                           FIXTURE_ORGS, users)
        await conn.execute("DELETE FROM config WHERE org_id = ANY($1::uuid[])", FIXTURE_ORGS)
        await conn.execute("DELETE FROM member_todos WHERE user_id = ANY($1::uuid[]) OR org_id = ANY($2::uuid[])",
                           users, FIXTURE_ORGS)
        await delete_tree(conn, "public.users", "id = ANY($1::uuid[]) OR auth0_sub LIKE $2",
                          [users, f"%{TAG}%"], log=log)
        await delete_tree(conn, "public.organizations", "id = ANY($1::uuid[])", [FIXTURE_ORGS], log=log)
    return log


async def leftovers(conn) -> int:
    n = 0
    for sql, args in (
        ("SELECT count(*) FROM users WHERE id = ANY($1::uuid[]) OR auth0_sub LIKE $2", [list(USERS), f"%{TAG}%"]),
        ("SELECT count(*) FROM organizations WHERE id = ANY($1::uuid[])", [FIXTURE_ORGS]),
        ("SELECT count(*) FROM deals WHERE org_id = ANY($1::uuid[])", [FIXTURE_ORGS]),
        ("SELECT count(*) FROM deal_signatures WHERE org_id = ANY($1::uuid[])", [FIXTURE_ORGS]),
        ("SELECT count(*) FROM deal_access WHERE org_id = ANY($1::uuid[])", [FIXTURE_ORGS]),
        ("SELECT count(*) FROM entities WHERE org_id = ANY($1::uuid[])", [FIXTURE_ORGS]),
        ("SELECT count(*) FROM config WHERE org_id = ANY($1::uuid[])", [FIXTURE_ORGS]),
        ("SELECT count(*) FROM audit_log WHERE org_id = ANY($1::uuid[])", [FIXTURE_ORGS]),
    ):
        n += int(await platform(conn, sql, *args, fetch="fetchval"))
    return n


# ═══ Seed ═══════════════════════════════════════════════════════════════════
SEEDED_TABLES = ("public.users", "public.entities", "public.delegate_grants", "public.compliance_records",
                 "public.entity_addresses", "public.deals", "public.config", "public.user_roles")


async def y_constraints(conn):
    section("[Y] Seed constraints read from pg_constraint / pg_index before seeding")
    rows = await conn.fetch(
        """
        SELECT conrelid::regclass::text AS t, conname, pg_get_constraintdef(oid) AS d FROM pg_constraint
        WHERE conrelid = ANY($1::regclass[]) AND contype IN ('u', 'c', 'x')
        UNION ALL
        SELECT indrelid::regclass::text, indexrelid::regclass::text, pg_get_indexdef(indexrelid) FROM pg_index
        WHERE indrelid = ANY($1::regclass[]) AND indisunique AND NOT indisprimary
        """, list(SEEDED_TABLES))
    for r in rows:
        print(f"[INFO] {r['t']}: {r['conname']} — {r['d'][:140]}")
    have = {r["conname"] for r in rows}
    check("compliance_records_entity_id_key" in have,
          "compliance_records is UNIQUE per entity — the seed writes exactly one row per entity", f"{len(rows)} read")
    check("deals_slug_key" in have, "deals.slug is UNIQUE — deals are created through the API (unique_slug)")
    check(any(r["t"] in ("users", "public.users") and "email" in r["d"] for r in rows),
          "users.email is UNIQUE — every fixture email carries the sprint tag")


async def seed(conn) -> None:
    from services import rbac

    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        for org, nm in ((ORG_A, "a"), (ORG_B, "b")):
            await conn.execute("INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3)",
                               org, f"DEALROOM1 Verify Org {nm.upper()}", f"{TAG}-{nm}")
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
        for uid, (org, account_role, nm, rname, full) in USERS.items():
            await conn.execute(
                "INSERT INTO users (id, org_id, email, full_name, auth0_sub, role, status, is_active) "
                "VALUES ($1, $2, $3, $4, $5, $6, 'active', true)",
                uid, org, email_of(uid), full, sub_of(uid), account_role)
            if rname:
                await conn.execute("INSERT INTO user_roles (user_id, role_id) VALUES ($1, $2)", uid, roles[(org, rname)])
        await rbac.grant_org_admin(conn, U_ORGADMIN, ORG_A)

        # publisher firms (with an address so the NDA prefill has state + address)
        for eid, name in ((E_PUB, "DR1 Acme Advisors LLC"), (E_OTHERPUB, "DR1 Other Sponsor LP")):
            await conn.execute("INSERT INTO entities (id, org_id, entity_type, display_name, legal_name) "
                               "VALUES ($1, $2, 'llc', $3, $3)", eid, ORG_A, name)
            await conn.execute("INSERT INTO entity_addresses (org_id, entity_id, street1, city, state, country, "
                               "is_primary) VALUES ($1, $2, '1 Main St', 'Wilmington', 'DE', 'US', true)", ORG_A, eid)
        # investors: each a CRM person linked to its user by a delegate grant, with an address
        comp = {  # entity → (kyc, ofac, accreditation)
            E_MEM: ("approved", "passed", "third_party_verified"),
            E_UNACC: ("approved", "passed", "not_verified"),
            E_KYC: ("in_progress", "passed", "third_party_verified"),
            E_NAMED: ("approved", "passed", "self_certified"),
            E_APPROVED: ("approved", "passed", "third_party_verified"),
            E_B: ("approved", "passed", "third_party_verified"),
        }
        for uid, eid in INVESTOR_ENTITY.items():
            org = USERS[uid][0]
            await conn.execute("INSERT INTO entities (id, org_id, entity_type, display_name, legal_name) "
                               "VALUES ($1, $2, 'individual', $3, $3)", eid, org, USERS[uid][4])
            await conn.execute("INSERT INTO entity_addresses (org_id, entity_id, street1, city, state, country, "
                               "is_primary) VALUES ($1, $2, '9 Elm St', 'Boston', 'MA', 'US', true)", org, eid)
            await conn.execute("INSERT INTO delegate_grants (org_id, principal_entity_id, delegate_user_id, scope) "
                               "VALUES ($1, $2, $3, 'transact')", org, eid, uid)
            kyc, ofac, acc = comp[eid]
            await conn.execute(
                "INSERT INTO compliance_records (org_id, entity_id, kyc_status, ofac_screen_status, "
                "accreditation_status, accreditation_basis, accreditation_verified_date) "
                "VALUES ($1, $2, $3::kyc_status, $4::ofac_status, $5::accreditation_status, 'income', "
                "CASE WHEN $5 = 'not_verified' THEN NULL ELSE current_date - 30 END)", org, eid, kyc, ofac, acc)
        # the org B deal (direct seed; its room is initialised through the service)
        await conn.execute("INSERT INTO entities (id, org_id, entity_type, display_name) VALUES ($1, $2, "
                           "'corporation', 'DR1 Org B Sponsor')", _id(160), ORG_B)
        await conn.execute("INSERT INTO deals (id, org_id, name, slug, deal_status, publisher_entity_id) "
                           "VALUES ($1, $2, 'DR1 Org B Deal', $3, 'active', $4)", D_B, ORG_B, f"{TAG}-b", _id(160))
    from services import deal_room as R

    async with conn.transaction():
        await conn.execute("SELECT set_config('app.current_org_id', $1, true), "
                           "set_config('app.is_super_admin', 'false', true)", str(ORG_B))
        await R.init_deal_room(conn, ORG_B, D_B)


# ═══ The real app, token stubbed by bearer string ══════════════════════════
CLAIMS_BY_TOKEN: dict[str, dict] = {}
R2_UPLOADS: list = []


def token_for(uid: UUID) -> str:
    c = {"sub": sub_of(uid), "org_id": str(USERS[uid][0])}
    key = "t" + str(abs(hash(json.dumps(c, sort_keys=True))))
    CLAIMS_BY_TOKEN[key] = c
    return key


async def api(uid, method, path, body=None, files=None, timeout=90.0):
    import main

    kw = {"headers": {"Authorization": f"Bearer {token_for(uid)}", "User-Agent": "verify_dealroom1"}}
    if body is not None:
        kw["json"] = body
    if files is not None:
        kw["files"] = files
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app, raise_app_exceptions=False),
                                 base_url="http://verify", timeout=timeout) as client:
        try:
            res = await asyncio.wait_for(client.request(method, path, **kw), timeout)
        except asyncio.TimeoutError:
            return 599, {"timeout": True}
    try:
        parsed = json.loads(res.text)
    except ValueError:
        parsed = {"raw": res.text}
    return res.status_code, parsed


def install_stubs():
    import main
    from services import storage
    from services import users as users_mod

    main.verify_token = lambda tok: dict(CLAIMS_BY_TOKEN[tok])
    from services.brief_blocks import register_brief_blocks

    register_brief_blocks()

    async def no_profile(request, c):
        return None

    users_mod.fetch_auth0_profile = no_profile

    def no_r2(*a, **k):
        raise AssertionError("verify: real R2 must never be called")

    def fake_upload(key, data, content_type=None, bucket=None):
        R2_UPLOADS.append((key, len(data), bucket))
        return key

    storage.get_s3_client = no_r2
    storage.delete_object = no_r2
    storage.upload_bytes = fake_upload
    storage.get_signed_url = lambda key, expires=3600, bucket=None: f"https://r2.mock/{bucket}/{key}?exp={expires}"


def D1() -> str:
    return S["d1"]


async def live_access_id(conn, deal, uid):
    return await platform(conn, "SELECT id FROM deal_access WHERE deal_id = $1 AND user_id = $2 AND valid_to IS NULL",
                          UUID(str(deal)), uid, fetch="fetchval")


async def room(uid, deal=None):
    return await api(uid, "GET", f"/api/v1/deal-room/deals/{deal or D1()}")


def file_names(envelope) -> set:
    return {d["file_name"] for f in (envelope or {}).get("folders", []) for d in f.get("documents", [])}


# ═══ Sections ══════════════════════════════════════════════════════════════
async def y_task1(conn):
    section("[Y] Task 1 — findings 1a–1e reported, each with its file and query")
    txt = (REPO / "docs" / "DEALROOM1_DISCOVERY.md").read_text(encoding="utf-8")
    for key, needles in {
        "1a": ("delegate_grants", "visible_entity_ids", "services/ownership_tree.py", "SELECT"),
        "1b": ("routers/marketplace.py", "manage_deals", "sponsor_entity_id", "insert_deal"),
        "1c": ("DocumentsList.jsx", "upload_document", "visible_to_members", "deals/{deal_id}/"),
        "1d": ("member_todos", "notification_bus", "routers/dashboard.py"),
        "1e": ("pdfplumber", "grep", "HTML"),
    }.items():
        part = re.search(rf"^## {key}\..*?(?=^## |\Z)", txt, re.S | re.M)
        body = part.group(0) if part else ""
        check(bool(part) and all(n in body for n in needles), f"{key} reported with its file(s) and query",
              f"missing {[n for n in needles if n not in body]}")


async def y_migration(conn):
    section("[Y] Migration — every existing deal: publisher, nine folders, documents mapped per the reported rule")
    report = json.loads((REPO / "docs" / "DEALROOM1_MIGRATION.json").read_text(encoding="utf-8"))
    rep_ids = {UUID(d["deal_id"]) for d in report["deals"]}
    live = await platform(conn, """
        SELECT d.id, d.publisher_entity_id,
               (SELECT count(*) FROM deal_folders f WHERE f.deal_id = d.id AND f.system_to IS NULL
                AND f.is_standard) AS nine,
               (SELECT count(*) FROM deal_room_settings s WHERE s.deal_id = d.id AND s.valid_to IS NULL) AS st,
               (SELECT count(*) FROM entity_publishers p WHERE p.entity_id = d.publisher_entity_id
                AND p.revoked_at IS NULL) AS cap
        FROM deals d WHERE d.valid_to IS NULL AND d.system_to IS NULL AND NOT (d.org_id = ANY($1::uuid[]))
          AND d.created_at <= (SELECT max(granted_at) FROM entity_publishers WHERE is_house)""", FIXTURE_ORGS)
    check(len(live) > 0 and {r["id"] for r in live} <= rep_ids,
          "every pre-sprint live deal is in the migration report", f"{len(live)} live, {len(rep_ids)} reported")
    bad = [str(r["id"]) for r in live if not (r["publisher_entity_id"] and r["nine"] == 9 and r["st"] == 1
                                               and r["cap"] == 1)]
    check(len(live) > 0 and not bad, "each has a publisher (with the capability), nine standard folders, one "
                                      "settings row — counts equal SQL", f"bad {bad}")
    by_deal = {UUID(d["deal_id"]): d for d in report["deals"]}
    mism = [str(r["id"]) for r in live if str(r["publisher_entity_id"]) != by_deal[r["id"]]["publisher_entity_id"]]
    check(not mism, "the publisher on each deal is the one the report names", f"{mism}")
    docs = report["documents"]
    rows = await platform(conn, """
        SELECT dd.id, dd.visible_to_members, f.key, f.access_level FROM deal_documents dd
        JOIN deal_folders f ON f.id = dd.folder_id WHERE dd.id = ANY($1::uuid[])""",
                          [UUID(d["document_id"]) for d in docs])
    got = {str(r["id"]): r for r in rows}
    wrong = [d["document_id"] for d in docs if "folder_key" in d and not (
        d["document_id"] in got and got[d["document_id"]]["key"] == d["folder_key"]
        and got[d["document_id"]]["access_level"] == d["access_level"]
        and (d["access_level"] == ("after_agreements" if got[d["document_id"]]["visible_to_members"]
                                   else "after_approval")))]
    check(len(docs) == len(rows) and not wrong,
          "every reported document sits in the folder and level the rule gives (true → 09 Other / after_agreements;"
          " false → 09 Other / Restricted / after_approval)", f"{len(docs)} reported, {len(rows)} found, wrong {wrong}")
    none_left = await platform(conn, "SELECT count(*) FROM deal_documents WHERE folder_id IS NULL AND NOT "
                                     "(org_id = ANY($1::uuid[])) AND created_at <= (SELECT max(granted_at) FROM "
                                     "entity_publishers WHERE is_house)", FIXTURE_ORGS, fetch="fetchval")
    check(int(none_left) == 0, "no pre-sprint document is left without a folder", f"{none_left}")
    for d in report["deals"]:
        print(f"[INFO] {d['name']}: publisher {d['publisher_entity_id']} ({d['publisher_rule']})")
    find("no live deal had a sponsor or a creator CRM person, so all nine took the org's house publisher",
         "docs/DEALROOM1_DISCOVERY.md 1b")


async def y_templates(conn):
    section("[Y] Templates — seeded from the docs file (v1, DRAFT); rendering fills every field")
    from services import deal_room_templates as T

    parsed = {t["key"]: t for t in T.parse_templates()}
    rows = await platform(conn, "SELECT id, key, version, status, scope, org_id, body, fields, source_sha256, "
                                "source_path FROM agreement_templates WHERE key = ANY($1::text[]) AND org_id IS NULL",
                          ["platform_nda", "platform_joinder"])
    by_key = {r["key"]: r for r in rows}
    S["templates"] = {k: r["id"] for k, r in by_key.items()}
    for key in ("platform_nda", "platform_joinder"):
        r = by_key.get(key)
        check(r is not None and r["version"] == 1 and r["status"] == "draft" and r["scope"] == "platform",
              f"{key} v1 is seeded, platform scope, status DRAFT")
        check(r is not None and r["body"] == parsed[key]["body"] and r["source_sha256"] == parsed[key]["source_sha256"]
              and r["source_path"] == "docs/templates/deal_room_templates_v1.md",
              f"{key} body and source hash equal a fresh parse of docs/templates/deal_room_templates_v1.md")
        t = parsed[key]
        check(sorted(T.body_field_keys(t["body"])) == sorted(f["key"] for f in t["fields"]),
              f"{key}: every {{{{field}}}} in the body is a declared field and vice versa")
        vals = {f["key"]: (f["default"] if f["default"] is not None else f"<{f['key']}>") for f in t["fields"]}
        text = T.render(t, vals)
        check("{{" not in text and "[optional" not in text and all(f"<{k}>" in text for k, v in vals.items()
                                                                 if v == f"<{k}>" and k in text_keys(t, vals)),
              f"{key}: rendering fills every field (no placeholder left)")
        miss = T.missing_fields(t, {})
        check(len(miss) > 0 and "Missing required field(s)" in raises(lambda: T.render(t, {})),
              f"{key}: a missing required field blocks rendering with a named message", miss[0]["label"])
    nda = parsed["platform_nda"]
    on = T.render(nda, {**{f["key"]: f"x{f['key']}" for f in nda["fields"]}, "non_circumvention": True})
    off = T.render(nda, {**{f["key"]: f"x{f['key']}" for f in nda["fields"]}, "non_circumvention": False})
    check("Non-circumvention." in on and "Non-circumvention." not in off,
          "the file's [optional] paragraph renders only when its switch is on")


def text_keys(t, vals):
    from services import deal_room_templates as T

    return set(re.findall(r"\{\{(\w+)\}\}", T._resolve_ifs(t["body"], vals)))


def raises(fn) -> str:
    try:
        fn()
    except Exception as exc:  # noqa: BLE001
        return str(exc)
    return ""


async def y_setup(conn):
    section("[Y] Publishers, a deal created by its publisher, delegates, agreements, folder documents")
    code, body = await api(U_MEMBER, "POST", "/api/v1/deal-room/publishers",
                           {"entity_id": str(E_PUB), "user_ids": [str(U_PUB)]})
    check(code == 403 and "manage_deals" in dumps(body), "a member cannot grant the publisher capability (403)",
          f"HTTP {code}")
    for eid, uid in ((E_PUB, U_PUB), (E_OTHERPUB, U_OTHERPUB)):
        code, body = await api(U_STAFF, "POST", "/api/v1/deal-room/publishers",
                               {"entity_id": str(eid), "user_ids": [str(uid)]})
        check(code == 201, f"staff makes {eid} a publisher with its user", f"HTTP {code} {dumps(body)}")
    deal = {"name": "DR1 Project X", "description": "teaser", "target_raise": 1000000}
    code, body = await api(U_MEMBER, "POST", "/api/v1/deal-room/deals", deal)
    check(code == 403, "create-deal: a member who acts for no publisher is refused (403)", f"HTTP {code}")
    code, body = await api(U_PUB, "POST", "/api/v1/deal-room/deals", deal)
    check(code == 201 and body.get("publisher_entity_id") == str(E_PUB),
          "the publisher's user creates a deal; publisher_entity_id = the entity they act for", dumps(body))
    S["d1"] = body.get("id")
    code, body = await api(U_OTHERPUB, "POST", "/api/v1/deal-room/deals", {"name": "DR1 Other Deal"})
    check(code == 201, "another publisher creates its own deal", f"HTTP {code}")
    S["d_other"] = body.get("id")
    await platform(conn, "UPDATE deals SET deal_status = 'active' WHERE id = ANY($1::uuid[]) AND system_to IS NULL",
                   [UUID(S["d1"]), UUID(S["d_other"])], fetch="execute")
    nine = await platform(conn, "SELECT count(*) FROM deal_folders WHERE deal_id = $1 AND is_standard "
                                "AND system_to IS NULL", UUID(D1()), fetch="fetchval")
    check(int(nine) == 9, "a new deal gets its nine standard folders on creation", f"{nine}")
    st = await platform(conn, "SELECT approval_mode, eligibility_rule FROM deal_room_settings WHERE deal_id = $1 "
                              "AND valid_to IS NULL", UUID(D1()), fetch="fetchrow")
    check(st is not None and st["approval_mode"] == "automatic" and st["eligibility_rule"] == "accredited",
          "defaults: approval automatic, eligibility accredited", dumps(dict(st) if st else None))
    code, _ = await api(U_PUB, "POST", f"/api/v1/deal-room/deals/{D1()}/delegates", {"user_id": str(U_DELEG)})
    check(code == 201, "the publisher adds a delegate", f"HTTP {code}")

    folders = {r["key"]: r["id"] for r in await platform(
        conn, "SELECT key, id FROM deal_folders WHERE deal_id = $1 AND system_to IS NULL", UUID(D1()))}
    S["folders"] = folders
    S["docs"] = {}
    for key, fname in (("01_overview", "dr1-public.pdf"), ("02_offering_memorandum", "dr1-agreements.pdf"),
                       ("05_legal_structure", "dr1-approval.pdf"), ("08_subscription", "dr1-named.pdf")):
        code, body = await api(U_PUB, "POST", f"/api/v1/deal-room/deals/{D1()}/folders/{folders[key]}/documents",
                               files={"file": (fname, b"%PDF-1.4 fixture", "application/pdf")})
        check(code == 201 and "r2_key" not in body, f"publisher uploads {fname} into {key} (R2 mocked)", f"HTTP {code}")
        S["docs"][fname] = body.get("id")
    check(len(R2_UPLOADS) >= 4 and all(k.startswith(f"deals/{D1()}/") for k, _, _ in R2_UPLOADS),
          "uploads went to the R2 recorder under deals/{deal}/{folder}/…", f"{len(R2_UPLOADS)}")

    # agreements: NDA (publisher entity type missing → blocked), then joinder
    code, body = await api(U_PUB, "POST", f"/api/v1/deal-room/deals/{D1()}/agreements",
                           {"template_id": str(S["templates"]["platform_nda"])})
    S["a_nda"] = body.get("id")
    check(code == 201, "the publisher requires the platform NDA", f"HTTP {code} {dumps(body)}")
    code, body = await api(U_PUB, "POST", f"/api/v1/deal-room/deals/{D1()}/agreements/{S['a_nda']}/publish")
    check(code == 422 and "Missing required field(s)" in dumps(body) and "Publisher entity type" in dumps(body),
          "publishing with a missing required field is blocked with a named message", dumps(body))
    code, body = await api(U_PUB, "PATCH", f"/api/v1/deal-room/deals/{D1()}/agreements/{S['a_nda']}",
                           {"field_values": {"publisher_entity_type": "limited liability company"}})
    check(code == 200 and body.get("blockers") == [], "once filled, no blocker remains", dumps(body))
    code, _ = await api(U_PUB, "POST", f"/api/v1/deal-room/deals/{D1()}/agreements/{S['a_nda']}/publish")
    check(code == 200, "the NDA publishes", f"HTTP {code}")
    code, body = await api(U_PUB, "POST", f"/api/v1/deal-room/deals/{D1()}/agreements", {
        "template_id": str(S["templates"]["platform_joinder"]),
        "field_values": {"underlying_counterparty": "Target Co", "underlying_nda_date": "August 21, 2026",
                         "underlying_nda_attached": False}})
    S["a_join"] = body.get("id")
    code2, _ = await api(U_PUB, "POST", f"/api/v1/deal-room/deals/{D1()}/agreements/{S['a_join']}/publish")
    check(code == 201 and code2 == 200, "the joinder is required second and publishes", f"{code} {code2}")


async def request(uid, deal=None):
    return await api(uid, "POST", f"/api/v1/deal-room/deals/{deal or D1()}/access/request",
                     {"entity_id": str(INVESTOR_ENTITY[uid])})


async def sign_all(uid, deal=None):
    out = []
    for aid in (S["a_nda"], S["a_join"]):
        code, doc = await api(uid, "GET", f"/api/v1/deal-room/deals/{deal or D1()}/agreements/{aid}/render")
        code2, res = await api(uid, "POST", f"/api/v1/deal-room/deals/{deal or D1()}/agreements/{aid}/sign", {
            "typed_name": USERS[uid][4], "consent": True, "rendered_text_sha256": doc.get("rendered_text_sha256")})
        out.append((code, code2, res))
    return out


async def y_eligibility(conn):
    section("[Y] Eligibility — unaccredited → not_eligible + staff to-do; KYC not clear → not_eligible; "
            "accredited → proceeds")
    code, body = await request(U_UNACC)
    check(code == 200 and body.get("status") == "not_eligible" and any("Accreditation" in m for m in body["missing"]),
          "unaccredited entity → not_eligible, told what is missing", dumps(body))
    code, env = await room(U_UNACC)
    check(code == 200 and file_names(env) == {"dr1-public.pdf"},
          "nothing opens for it beyond the public folder", f"{sorted(file_names(env))}")
    locked = [f for f in env.get("folders", []) if not f["open"]]
    check(len(locked) > 0 and all(f.get("lock_reason") for f in locked) and all("documents" not in f for f in locked),
          "every locked folder states its reason and carries no document list")
    todos = await platform(conn, "SELECT user_id FROM member_todos WHERE source = 'deal_room' AND related_id = $1 "
                                 "AND action_params->>'kind' = 'not_eligible' AND action_params->>'investor_user_id' = $2"
                                 " AND status = 'open'", UUID(D1()), str(U_UNACC))
    check(U_STAFF in {t["user_id"] for t in todos}, "a staff to-do is opened (re-read)", f"{len(todos)} to-dos")
    code, body = await request(U_KYC)
    check(code == 200 and body.get("status") == "not_eligible" and any("KYC" in m for m in body["missing"]),
          "KYC not clear → not_eligible even though accredited", dumps(body))
    code, body = await request(U_MEMBER)
    check(code == 200 and body.get("status") == "requested" and body.get("eligible") is True,
          "accredited entity with KYC/OFAC clear → proceeds to signing", dumps(body))
    code, body = await api(U_MEMBER, "POST", f"/api/v1/deal-room/deals/{D1()}/access/request",
                           {"entity_id": str(E_UNACC)})
    check(code == 404, "an investor cannot pick an entity outside their own investing entities", f"HTTP {code}")


async def y_signing(conn):
    section("[Y] Signing — hash recorded; typed name + consent required; append-only (RLS + trigger); chain verifier")
    from services import deal_room as R

    base = f"/api/v1/deal-room/deals/{D1()}/agreements"
    code, doc = await api(U_MEMBER, "GET", f"{base}/{S['a_nda']}/render")
    sha = doc.get("rendered_text_sha256")
    check(code == 200 and sha == hashlib.sha256(doc.get("text", "").encode("utf-8")).hexdigest()
          and "{{" not in doc.get("text", "") and "Jane Accredited" in doc.get("text", ""),
          "the rendered agreement is fully filled and its hash is SHA-256 of exactly that text")
    good = {"typed_name": "Jane Accredited", "consent": True, "rendered_text_sha256": sha}
    for label, patch, want in (("consent unticked", {"consent": False}, 422), ("no typed name", {"typed_name": " "}, 422),
                               ("a different name", {"typed_name": "Someone Else"}, 422),
                               ("a stale text hash", {"rendered_text_sha256": "0" * 64}, 409)):
        code, _ = await api(U_MEMBER, "POST", f"{base}/{S['a_nda']}/sign", {**good, **patch})
        check(code == want, f"signing refused with {label} ({want})", f"HTTP {code}")
    code, _ = await api(U_MEMBER, "GET", f"{base}/{S['a_join']}/render")
    code2, _ = await api(U_MEMBER, "POST", f"{base}/{S['a_join']}/sign", good)
    check(code2 == 409, "agreements are signed in order (the joinder before the NDA is refused)", f"HTTP {code2}")
    n_before = await scoped(conn, ORG_A, "SELECT count(*) FROM deal_signatures WHERE deal_id = $1", UUID(D1()),
                            fetch="fetchval")
    check(int(n_before) == 0, "no signature was written by any refused attempt", f"{n_before}")
    code, res = await api(U_MEMBER, "POST", f"{base}/{S['a_nda']}/sign", good)
    check(code == 201 and res.get("complete") is False, "the NDA is signed", dumps(res))
    sig = await scoped(conn, ORG_A, f"SELECT {R.SIGNATURE_COLS} FROM deal_signatures WHERE id = $1",
                       UUID(res.get("signature_id")), fetch="fetchrow")
    check(sig is not None and sig["rendered_text_sha256"] == sha and sig["rendered_text"] == doc.get("text")
          and sig["consent"] is True and sig["typed_name"] == "Jane Accredited" and sig["chain"] == "test"
          and sig["user_agent"] == "verify_dealroom1",
          "independent re-read: the row holds the shown text, its hash, consent, typed name, user agent (test chain)")
    check(sig is not None and R.record_sha256(dict(sig)) == sig["record_sha256"],
          "record_sha256 = SHA-256 over the canonical record including prev_signature_sha256")
    S["sig1"] = sig["id"] if sig else None

    role = await conn.fetchrow("SELECT current_user AS u, rolbypassrls FROM pg_roles WHERE rolname = current_user")
    check(role["u"] == "app_service" and role["rolbypassrls"] is False,
          "the verify connection is app_service with rolbypassrls = false", dumps(dict(role)))
    upd = await scoped(conn, ORG_A, "UPDATE deal_signatures SET typed_name = 'Forged' WHERE id = $1", sig["id"],
                       fetch="execute")
    dele = await scoped(conn, ORG_A, "DELETE FROM deal_signatures WHERE id = $1", sig["id"], fetch="execute")
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.current_org_id', $1, true), "
                           "set_config('dealroom.signature_teardown', 'fixture', true)", str(ORG_A))
        guc_del = await conn.execute("DELETE FROM deal_signatures WHERE id = $1", sig["id"])
    after = await scoped(conn, ORG_A, "SELECT typed_name FROM deal_signatures WHERE id = $1", sig["id"],
                         fetch="fetchval")
    check(upd == "UPDATE 0" and dele == "DELETE 0" and guc_del == "DELETE 0" and after == "Jane Accredited",
          "RLS: app_service UPDATE / DELETE match zero rows (no policy) — even with the teardown GUC set; row unchanged",
          f"{upd} / {dele} / {guc_del}")
    op = await operator_conn()
    try:
        trig = []
        for sql in ("UPDATE deal_signatures SET typed_name = 'Forged' WHERE id = $1",
                    "DELETE FROM deal_signatures WHERE id = $1"):
            try:
                async with op.transaction():
                    await op.execute(sql, sig["id"])
                trig.append("no error")
            except Exception as exc:  # noqa: BLE001
                trig.append(type(exc).__name__ + ": " + str(exc)[:60])
        check(all("append-only" in t for t in trig),
              "trigger: a bypass-RLS session (postgres) is refused both UPDATE and DELETE without the teardown path",
              f"{trig}")
    finally:
        await op.close()

    code, res = await api(U_MEMBER, "POST", f"{base}/{S['a_join']}/sign",
                          {**good, "rendered_text_sha256": (await api(U_MEMBER, "GET", f"{base}/{S['a_join']}/render"))[1]
                           .get("rendered_text_sha256")})
    check(code == 201 and res.get("complete") is True, "the joinder is signed — every agreement done", dumps(res))

    async def chain(name):
        return await platform(conn, f"SELECT {R.SIGNATURE_COLS} FROM deal_signatures WHERE chain = $1 "
                                    "ORDER BY chain_seq", name)
    rows = await chain("test")
    v = R.verify_chain_rows(rows)
    check(v["ok"] is True and v["count"] >= 2, "the chain verifier passes on the test chain", dumps(v))
    code, body = await api(U_SUPER, "GET", "/api/v1/deal-room/signatures/verify?chain=test")
    check(code == 200 and body.get("ok") is True, "GET /deal-room/signatures/verify (Super Admin) agrees", dumps(body))
    code, _ = await api(U_MEMBER, "GET", "/api/v1/deal-room/signatures/verify?chain=test")
    check(code == 403, "a member cannot run the verifier route", f"HTTP {code}")
    S["platform_chain"] = R.verify_chain_rows(await chain("platform"))

    op = await operator_conn()
    try:
        async with op.transaction():
            await op.execute("SELECT set_config('dealroom.signature_teardown', 'fixture', true)")
            await op.execute("UPDATE deal_signatures SET typed_name = typed_name || ' (tampered)' WHERE id = $1",
                             sig["id"])
        v2 = R.verify_chain_rows(await chain("test"))
        check(v2["ok"] is False and v2["first_break"]["id"] == str(sig["id"])
              and "record_sha256" in v2["first_break"]["reason"],
              "a tampered fixture row is flagged as the FIRST break, by id", dumps(v2))
        async with op.transaction():
            await op.execute("SELECT set_config('dealroom.signature_teardown', 'fixture', true)")
            await op.execute("UPDATE deal_signatures SET typed_name = 'Jane Accredited' WHERE id = $1", sig["id"])
    finally:
        await op.close()
    check(R.verify_chain_rows(await chain("test"))["ok"] is True, "restored → the chain verifies again")

    code, page = await api(U_MEMBER, "GET", f"/api/v1/deal-room/signatures/{sig['id']}/copy")
    raw = page.get("raw", "") if isinstance(page, dict) else ""
    check(code == 200 and sig["rendered_text_sha256"] in raw and "Jane Accredited" in raw,
          "the investor downloads the signed copy (HTML with the signature block)")
    code, _ = await api(U_PUB, "GET", f"/api/v1/deal-room/signatures/{sig['id']}/copy")
    code2, _ = await api(U_UNACC, "GET", f"/api/v1/deal-room/signatures/{sig['id']}/copy")
    check(code == 200 and code2 == 404, "the publisher can download it; another investor cannot (404)",
          f"{code} {code2}")


async def y_approval(conn):
    section("[Y] Approval — automatic grants after_agreements; publisher mode waits; after_approval / named_only "
            "need explicit decisions")
    acc = await scoped(conn, ORG_A, "SELECT status, level_granted FROM deal_access WHERE deal_id = $1 AND user_id = $2"
                                    " AND valid_to IS NULL", UUID(D1()), U_MEMBER, fetch="fetchrow")
    check(acc is not None and acc["status"] == "granted" and acc["level_granted"] == "after_agreements",
          "automatic (default): the last signature granted after_agreements (re-read)", dumps(dict(acc) if acc else None))
    code, _ = await api(U_PUB, "PUT", f"/api/v1/deal-room/deals/{D1()}/settings", {"approval_mode": "publisher"})
    check(code == 200, "the publisher switches the deal to publisher approval", f"HTTP {code}")
    for uid in (U_APPROVED, U_NAMED):
        await request(uid)
        res = await sign_all(uid)
        check(all(c2 == 201 for _, c2, _ in res), f"{USERS[uid][2]} signs both agreements",
              f"{[(c, c2) for c, c2, _ in res]}")
        st = await scoped(conn, ORG_A, "SELECT status FROM deal_access WHERE deal_id = $1 AND user_id = $2 "
                                       "AND valid_to IS NULL", UUID(D1()), uid, fetch="fetchval")
        check(st == "signed_awaiting", f"publisher mode: {USERS[uid][2]} waits in signed_awaiting", f"{st}")
    todo = await platform(conn, "SELECT user_id FROM member_todos WHERE source = 'deal_room' AND related_id = $1 AND "
                                "action_params->>'kind' = 'approve_request' AND status = 'open'", UUID(D1()))
    check({U_PUB, U_DELEG} <= {t["user_id"] for t in todo}, "to-dos opened for the publisher AND the delegate",
          f"{len(todo)}")
    code, env = await room(U_APPROVED)
    check(code == 200 and file_names(env) == {"dr1-public.pdf"}, "while waiting, nothing beyond public opens",
          f"{sorted(file_names(env))}")
    aid = await live_access_id(conn, D1(), U_APPROVED)
    code, body = await api(U_PUB, "POST", "/api/v1/deal-room/access/decide",
                           {"access_ids": [str(aid)], "action": "approve", "level": "after_approval"})
    check(code == 200 and body["results"][0]["ok"] is True, "the publisher approves at after_approval", dumps(body))
    nid = await live_access_id(conn, D1(), U_NAMED)
    code, body = await api(U_DELEG, "POST", "/api/v1/deal-room/access/decide",
                           {"access_ids": [str(nid)], "action": "approve", "level": "after_agreements"})
    check(code == 200 and body["results"][0]["ok"] is True, "the delegate approves the other at after_agreements",
          dumps(body))
    code, env = await room(U_NAMED)
    check("dr1-named.pdf" not in file_names(env), "named_only stays closed until the investor is named")
    code, _ = await api(U_PUB, "POST", f"/api/v1/deal-room/deals/{D1()}/folders/{S['folders']['08_subscription']}/named",
                        {"user_id": str(U_NAMED)})
    check(code == 201, "the publisher names the investor on 08 Subscription", f"HTTP {code}")
    open_todos = await platform(conn, "SELECT count(*) FROM member_todos WHERE source = 'deal_room' AND related_id = $1 "
                                      "AND action_params->>'kind' = 'approve_request' AND status = 'open'", UUID(D1()),
                                fetch="fetchval")
    check(int(open_todos) == 0, "deciding closes the approval to-dos", f"{open_todos}")


EXPECT = {  # caller → file names visible (D1)
    "publisher": {"dr1-public.pdf", "dr1-agreements.pdf", "dr1-approval.pdf", "dr1-named.pdf"},
    "staff": {"dr1-public.pdf", "dr1-agreements.pdf", "dr1-approval.pdf", "dr1-named.pdf"},
    "signed": {"dr1-public.pdf", "dr1-agreements.pdf"},
    "approved": {"dr1-public.pdf", "dr1-agreements.pdf", "dr1-approval.pdf"},
    "named": {"dr1-public.pdf", "dr1-agreements.pdf", "dr1-named.pdf"},
    "anonymous_to_deal": {"dr1-public.pdf"},
}
CALLERS = {"publisher": U_PUB, "staff": U_STAFF, "signed": U_MEMBER, "approved": U_APPROVED, "named": U_NAMED,
           "anonymous_to_deal": U_KYC}


async def y_folders(conn):
    section("[Y] Folder access — each level only to qualifying callers; downloads refused identically; no leaks")
    all_names = EXPECT["publisher"]
    for who, uid in CALLERS.items():
        code, env = await room(uid)
        got = file_names(env) & all_names
        check(code == 200 and got == EXPECT[who], f"{who}: listing shows exactly its levels", f"{sorted(got)}")
        closed = all_names - EXPECT[who]
        text = json.dumps(env, ensure_ascii=False)
        check(not any(n in text for n in closed), f"{who}: no closed folder leaks a file name anywhere in the response")
        dl, bodies = {}, set()
        for name, doc_id in S["docs"].items():
            c, b = await api(uid, "GET", f"/api/v1/deal-room/deals/{D1()}/documents/{doc_id}/download")
            dl[name] = c
            if c != 200:
                bodies.add(json.dumps(b, sort_keys=True))
            elif not str(b.get("url", "")).startswith("https://r2.mock/"):
                dl[name] = -1
        check({n for n, c in dl.items() if c == 200} == EXPECT[who] and all(c in (200, 404) for c in dl.values())
              and len(bodies) <= 1, f"{who}: signed-URL downloads allowed exactly there; every refusal the same 404",
              f"{dl}")
    code, _ = await room(U_OTHERORG)
    c2, _ = await api(U_OTHERORG, "GET", f"/api/v1/deal-room/deals/{D1()}/documents/{S['docs']['dr1-public.pdf']}/download")
    check(code == 404 and c2 == 404, "another org's member: the deal and even its public document are 404",
          f"{code} {c2}")
    # the same rule reaches GET /deals/{id} and Document Search
    code, det = await api(U_MEMBER, "GET", f"/api/v1/deals/{D1()}")
    names = {d["file_name"] for d in (det or {}).get("documents", [])}
    check(code == 200 and names & all_names == EXPECT["signed"],
          "GET /deals/{id} returns a member only the documents their folders open", f"{sorted(names)}")
    code, srch = await api(U_KYC, "GET", "/api/v1/document-search?q=dr1-")
    hits = {r.get("original_filename") for r in (srch or {}).get("results", [])}
    check(code == 200 and hits & all_names == EXPECT["anonymous_to_deal"],
          "Document Search applies the same folder rule", f"{sorted(hits)}")


async def y_investor_view(conn):
    section("[Y] Investor view — locked folders show the reason; no other investor's access, names or counts")
    code, env = await room(U_MEMBER)
    text = json.dumps(env, ensure_ascii=False)
    others = [USERS[u][4] for u in (U_APPROVED, U_NAMED, U_UNACC, U_KYC)]
    check(code == 200 and not any(n in text for n in others), "no other investor's name appears", f"{others}")
    check(set(env) & {"rows", "interest_count", "investors", "delegates", "settings"} == set()
          and env.get("vocabularies", {}).get("editable") == [] and env["permissions"]["can_write"] is False,
          "the investor envelope carries no grid rows, counts, delegates or settings; editable is []")
    locked = [f for f in env["folders"] if not f["open"]]
    check(len(locked) > 0 and all(f["lock_reason"] for f in locked), "each locked folder states why",
          f"{[(f['label'], f['lock_reason']) for f in locked]}")
    code, mine = await api(U_MEMBER, "GET", "/api/v1/deal-room/my-access")
    row = next((r for r in mine.get("rows", []) if r["deal_id"] == D1()), {})
    check(code == 200 and len(row.get("signatures", [])) == 2 and all(s["copy_href"] for s in row["signatures"])
          and not any(n in json.dumps(mine) for n in others),
          "My deal access lists the investor's deal, both signatures with dates and signed-copy links, nobody else's")


async def y_rights(conn):
    section("[Y] Rights matrix — identical requests on deal edit, document upload, settings and access actions")
    folder = S["folders"]["09_other"]
    expect = {
        U_MEMBER: (403, 403, 403, 403), U_PUB: (200, 201, 200, 200), U_DELEG: (200, 201, 200, 200),
        U_OTHERPUB: (403, 403, 403, 404), U_STAFF: (200, 201, 200, 200), U_ORGADMIN: (200, 201, 200, 200),
        U_SUPER: (200, 201, 200, 200), U_OTHERORG: (404, 404, 404, 403),
    }
    for uid, want in expect.items():
        before = await platform(conn, "SELECT description FROM deals WHERE id = $1 AND system_to IS NULL",
                                UUID(D1()), fetch="fetchval")
        e, _ = await api(uid, "PATCH", f"/api/v1/deal-room/deals/{D1()}", {"description": f"edited by {USERS[uid][2]}"})
        u, _ = await api(uid, "POST", f"/api/v1/deal-room/deals/{D1()}/folders/{folder}/documents",
                         files={"file": ("dr1-matrix.txt", b"x", "text/plain")})
        s, _ = await api(uid, "PUT", f"/api/v1/deal-room/deals/{D1()}/settings", {"sponsor_display": "name"})
        aid = await live_access_id(conn, D1(), U_MEMBER)
        a, _ = await api(uid, "POST", "/api/v1/deal-room/access/decide",
                         {"access_ids": [str(aid)], "action": "approve", "level": "after_agreements"})
        after = await platform(conn, "SELECT description FROM deals WHERE id = $1 AND system_to IS NULL",
                               UUID(D1()), fetch="fetchval")
        persisted = (after == f"edited by {USERS[uid][2]}") if want[0] == 200 else (after == before)
        check((e, u, s, a) == want and persisted,
              f"{USERS[uid][2]}: edit/upload/settings/decide → {want} (edit persisted iff allowed, re-read)",
              f"got {(e, u, s, a)}")
    code, _ = await api(U_PUB, "PUT", f"/api/v1/deals/{D1()}", {"description": "legacy"})
    check(code == 403, "the existing PUT /deals/{id} keeps its manage_deals gate (unchanged) — publishers edit "
                       "through /deal-room", f"HTTP {code}")
    find("existing marketplace routes keep their gates; publisher/delegate editing is the new /deal-room surface",
         "sprint rule 'never change an existing route's gate'")


async def y_grid(conn):
    section("[Y] Grid — scope per role; approve/decline/revoke immediate (re-read) and audited; investors refused")
    await request(U_MEMBER, S["d_other"])  # no agreements → automatic grant at once
    await request(U_OTHERORG, str(D_B))

    async def grid(uid):
        code, body = await api(uid, "GET", "/api/v1/deal-room/access-grid?page_size=200&group_by=deal")
        return code, {r["id"] for r in body.get("rows", [])} if code == 200 else set(), body

    async def sql_ids(where, *args):
        return {str(r["id"]) for r in await platform(conn, f"SELECT a.id FROM deal_access a JOIN deals d ON "
                                                           f"d.id = a.deal_id AND d.system_to IS NULL WHERE "
                                                           f"a.valid_to IS NULL AND {where}", *args)}
    fx = "a.org_id = ANY($1::uuid[])"
    cases = [
        (U_PUB, await sql_ids(f"{fx} AND a.deal_id = $2", FIXTURE_ORGS, UUID(D1()))),
        (U_DELEG, await sql_ids(f"{fx} AND a.deal_id = $2", FIXTURE_ORGS, UUID(D1()))),
        (U_OTHERPUB, await sql_ids(f"{fx} AND a.deal_id = $2", FIXTURE_ORGS, UUID(S["d_other"]))),
        (U_STAFF, await sql_ids("a.org_id = $1", ORG_A)),
        (U_ORGADMIN, await sql_ids("a.org_id = $1", ORG_A)),
    ]
    for uid, want in cases:
        code, got, _ = await grid(uid)
        check(code == 200 and len(want) > 0 and got == want, f"{USERS[uid][2]}: the grid shows exactly its scope "
                                                             "(equals SQL)", f"{len(got)} vs {len(want)}")
    code, got, body = await grid(U_SUPER)
    want = await sql_ids(fx, FIXTURE_ORGS)
    check(code == 200 and want <= got and any(r["firm_name"].endswith("Org B") for r in body["rows"]),
          "super admin sees every org (both fixture orgs)", f"{len(want & got)}/{len(want)}")
    for uid in (U_MEMBER, U_UNACC):
        code, _, body = await grid(uid)
        check(code == 403, f"{USERS[uid][2]} (investor, no deal of their own) is refused the grid (403)", f"HTTP {code}")
    code, body = await api(U_PUB, "GET", f"/api/v1/deal-room/access-grid?status=signed_awaiting&page_size=200")
    check(code == 200 and all(r["status"] == "signed_awaiting" for r in body["rows"]), "status filter narrows")
    code, body = await api(U_STAFF, "GET", "/api/v1/deal-room/access-grid?unsigned=true&page_size=200")
    ids = {r["id"] for r in body.get("rows", [])}
    want_unsigned = {str(await live_access_id(conn, D1(), u)) for u in (U_UNACC, U_KYC)}
    check(code == 200 and want_unsigned <= ids and str(await live_access_id(conn, D1(), U_MEMBER)) not in ids,
          "unsigned filter: includes the unsigned rows, excludes a fully-signed one")
    row = next((r for r in body.get("rows", []) if r["id"] in want_unsigned), {})
    check(set(row.get("agreements", {})) >= {"platform_nda", "platform_joinder"},
          "one column per required agreement (signed date or —)", dumps(row.get("agreements")))
    check(body.get("permissions", {}).get("can_write") is True and body["vocabularies"]["actions"] ==
          ["approve", "decline", "revoke"], "the envelope publishes can_write and the actions")

    # decline with reason, revoke — immediate, audited, signatures kept
    ua = await live_access_id(conn, D1(), U_UNACC)
    code, _ = await api(U_PUB, "POST", "/api/v1/deal-room/access/decide", {"access_ids": [str(ua)], "action": "decline"})
    check(code == 422, "decline needs a reason", f"HTTP {code}")
    code, body = await api(U_PUB, "POST", "/api/v1/deal-room/access/decide",
                           {"access_ids": [str(ua)], "action": "decline", "reason": "Not a fit"})
    st = await scoped(conn, ORG_A, "SELECT status, reason FROM deal_access WHERE deal_id = $1 AND user_id = $2 AND "
                                   "valid_to IS NULL", UUID(D1()), U_UNACC, fetch="fetchrow")
    check(code == 200 and st["status"] == "declined" and st["reason"] == "Not a fit", "decline takes effect (re-read)")
    sigs_before = await scoped(conn, ORG_A, "SELECT count(*) FROM deal_signatures WHERE deal_id = $1 AND user_id = $2",
                               UUID(D1()), U_MEMBER, fetch="fetchval")
    ma = await live_access_id(conn, D1(), U_MEMBER)
    code, body = await api(U_STAFF, "POST", "/api/v1/deal-room/access/decide",
                           {"access_ids": [str(ma), str(ua)], "action": "revoke"})
    check(code == 200 and [r["ok"] for r in body["results"]] == [True, False],
          "bulk revoke: the granted row is revoked; the declined one is refused per row", dumps(body))
    c, _ = await api(U_MEMBER, "GET", f"/api/v1/deal-room/deals/{D1()}/documents/{S['docs']['dr1-agreements.pdf']}/download")
    sigs_after = await scoped(conn, ORG_A, "SELECT count(*) FROM deal_signatures WHERE deal_id = $1 AND user_id = $2",
                              UUID(D1()), U_MEMBER, fetch="fetchval")
    check(c == 404 and int(sigs_after) == int(sigs_before) == 2,
          "revoke stops access immediately (download 404) and keeps both signatures", f"{c} {sigs_before}→{sigs_after}")
    hist = await platform(conn, "SELECT count(*) FROM deal_access WHERE deal_id = $1 AND user_id = $2", UUID(D1()),
                          U_MEMBER, fetch="fetchval")
    check(int(hist) >= 3, "history kept: every change is a new row, the old ones closed", f"{hist} rows")
    audits = await platform(conn, "SELECT action FROM audit_log WHERE org_id = $1 AND action LIKE 'deal_access.%'", ORG_A)
    acts = {a["action"] for a in audits}
    check({"deal_access.approve", "deal_access.decline", "deal_access.revoke", "deal_access.auto_grant"} <= acts,
          "every decision is audited in its own transaction", f"{sorted(acts)}")
    code, _ = await request(U_MEMBER)
    check(code == 409, "a revoked investor cannot re-request their way back (signatures would auto-grant)",
          f"HTTP {code}")


async def y_routes(conn):
    section("[Y] Every new route has a gate and is in AUTHZ1_ROUTES.json (--check CURRENT); no existing gate changed")
    before = json.loads(git_show(BEFORE_REF, "docs/AUTHZ1_ROUTES.json") or "{}")
    now = json.loads((REPO / "docs" / "AUTHZ1_ROUTES.json").read_text(encoding="utf-8"))

    def gates(d):
        return {(tuple(r.get("methods") or []), r["path"]): r for r in d.get("routes", [])}

    gb, gn = gates(before), gates(now)
    changed = [k for k in gb if k in gn and gb[k].get("after") != gn[k].get("after")]
    check(len(gb) > 0 and not changed, "no existing route's gate changed (AUTHZ1_ROUTES.json before vs now)",
          f"{changed[:5]}")
    new = [k for k in gn if k not in gb]
    check(len(new) > 0 and all(k[1].startswith("/api/v1/deal-room/") for k in new),
          "the new routes are exactly the /deal-room surface", f"{len(new)}")
    ungated = [k for k in new if gn[k]["after"] == "none beyond auth"
               and (k[0] != ("GET",) or k[1] not in ("/api/v1/deal-room/me", "/api/v1/deal-room/templates",
                                                      "/api/v1/deal-room/my-access"))]
    check(not ungated, "every new route names a gate (the three self-scoped GETs are require_active)", f"{ungated}")
    code, out, err = run_cmd([sys.executable, str(HERE / "authz1_routes.py"), "--check"], REPO, timeout=300)
    check(code == 0 and "CURRENT" in out, "authz1_routes.py --check: CURRENT", (out + err)[-200:])


async def y_front_end(conn):
    section("[Y] Front end — node tests for the investor flow and grid; npm run build")
    code, out, err = run_cmd(["node", "--test", "tests/dealroom/"], WEB_DIR, timeout=300)
    check(code == 0 and re.search(r"^# fail 0", out, re.M) is not None, "node --test tests/dealroom passes",
          (out + err)[-300:] if code else "")
    for name in ("investor flow: request → not eligible → sign in order → awaiting / granted",
                 "locked folders show the reason and NO file names; open ones list documents",
                 "publisher controls render only when can_write === true (no truthy fallback)",
                 "grid: approve / decline / revoke render from the envelope only"):
        check(re.search(rf"^ok \d+ - {re.escape(name)}", out, re.M) is not None, f"node test: {name}")
    code, out, err = run_cmd(["node", "--test", "tests/"], WEB_DIR, timeout=600)
    check(code == 0 and re.search(r"^# fail 0", out, re.M) is not None, "node --test tests/ passes (0 fail)",
          (out + err)[-300:] if code else "")
    code, out, err = run_cmd(["npm", "run", "build"], WEB_DIR, timeout=1200)
    check(code == 0, "npm run build exits 0", (out + err)[-400:] if code else "")


def regression():
    section("[Y] Regression — flat subprocesses AFTER teardown, VERIFY_NESTED=1")
    env = os.environ | {"VERIFY_NESTED": "1"}
    for script in ("verify_authz1.py", "verify_bugfix1.py", "verify_navregroup.py"):
        code, out, err = run_cmd([sys.executable, str(HERE / script)], REPO, timeout=1200, env=env)
        m = re.findall(r"^TOTAL: (\d+) PASS, (\d+) FAIL", out, re.M)
        fails = [ln for ln in out.splitlines() if ln.startswith("[FAIL]")]
        check(bool(m) and m[-1][1] == "0" and code == 0, f"{script}: 0 FAIL",
              f"{m[-1] if m else 'no TOTAL'}; {fails[:3]} {err[-200:] if not m else ''}")


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
    print(f"[INFO] database via {how}")
    conn = await connect(dsn)
    install_stubs()
    pre = gold = None
    try:
        await run("pre-teardown", teardown, conn)
        pre = await counts(conn)
        gold = await gold_fingerprint(conn)
        await run("constraints", y_constraints, conn)
        await run("seed", seed, conn)
        await run("task 1", y_task1, conn)
        await run("migration", y_migration, conn)
        await run("templates", y_templates, conn)
        await run("setup", y_setup, conn)
        if S.get("d1"):
            await run("eligibility", y_eligibility, conn)
            await run("signing", y_signing, conn)
            await run("approval", y_approval, conn)
            await run("folders", y_folders, conn)
            await run("investor view", y_investor_view, conn)
            await run("rights", y_rights, conn)
            await run("grid", y_grid, conn)
        else:
            check(False, "setup created the fixture deal", "every deal-scoped section skipped")
        await run("routes", y_routes, conn)
        await run("front end", y_front_end, conn)
    finally:
        section("[Y] Teardown — zero fixture rows; every touched table at its pre-test count")
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
            if gold is not None:
                check(await gold_fingerprint(conn) == gold, "real note_gold_values unchanged (count + checksum)",
                      f"{gold[0]} rows")
            if "platform_chain" in S:
                from services import deal_room as R

                rows = await platform(conn, f"SELECT {R.SIGNATURE_COLS} FROM deal_signatures WHERE chain = 'platform' "
                                            "ORDER BY chain_seq")
                check(R.verify_chain_rows(rows) == S["platform_chain"],
                      "the real 'platform' signature chain is untouched by the run")
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
