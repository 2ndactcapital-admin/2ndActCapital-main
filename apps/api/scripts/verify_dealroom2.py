"""verify_dealroom2 — Marketplace Deal Room, Phase 2 (DEALROOM2).

WRITTEN, NOT RUN by the sprint. The operator runs:

    doppler run -- apps/api/venv/bin/python apps/api/scripts/verify_dealroom2.py 2>&1 | grep -E "^\\[FAIL\\]|^TOTAL"

Pass/fail only, no prompts. Prints 'TOTAL: N PASS, M FAIL' and exits non-zero
on any failure. AUTHZ_TEST_MODE=1 is set before anything imports ``main``;
secrets are hydrated from Doppler over HTTPS (``_db_connect``) first. R2 is
mocked: ``upload_bytes`` stores the bytes in memory, ``download_bytes`` reads
them back, ``get_signed_url`` returns a fake URL, and ``get_s3_client`` /
``delete_object`` fail the run if anything reaches real R2.

Everything after the bearer token is the real app (middleware, Principal,
every gate, every handler, RLS) against the live database. The script's own
connection is DATABASE_URL = app_service (rolbypassrls = false, asserted).

FIXTURES: two fixture orgs (99000000-…-0000000d2001 / d2002). Every fixture
user is created explicitly with the auth0_sub its token carries and an
@test.local email (users.is_test = true by trigger), so every signature lands
in the 'test' chain, never the real 'platform' chain. Every deal, document,
agreement, version, template and master NDA is the run's own.

TEARDOWN (start and end): deal_signatures is append-only and agreement_versions
immutable (no app_service UPDATE / DELETE policy + a refusing trigger); a
firm template that left draft is kept forever (trigger). The ONLY removal path
is the privileged fixture path of docs/dealroom1_part1.sql / dealroom2_part1.sql:
the operator DSN (DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP, role postgres) with
dealroom.signature_teardown = 'fixture' in its transaction, scoped to the
fixture orgs (signatures: test chain only). DEALROOM3 moved the whole
teardown onto scripts/_verify_cleanup.py (one scoped transaction, FK closure
over this run's fixture ids only — no recursive FK walk); every counted table
is then re-read for its exact pre-test count; real note_gold_values, real
deals, real signatures (the platform chain) and real templates must be
unchanged.

With VERIFY_NESTED=1 the regression section prints one '[SKIP] nested run'.
"""

from __future__ import annotations

import os as _authz_os
_authz_os.environ["AUTHZ_TEST_MODE"] = "1"  # AUTHZ1: set before main is imported

import asyncio
import hashlib
import io
import json
import os
import pathlib
import re
import subprocess
import sys
import traceback
from datetime import date
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
import _verify_cleanup as VC  # noqa: E402

BEFORE_REF = "0823d72"   # dealroom2-work base — main after the DEALROOM1 merge + snapshot
DEALROOM2_REF = "994d8ce"  # this sprint's own commit (its routes, for the y_routes count)
TAG = "dealroom2verify"
OPERATOR_DSN_VAR = "DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP"


def _id(n: int) -> UUID:
    return UUID(f"99000000-0000-0000-0000-0000000d2{n:03d}")


ORG_A, ORG_B = _id(1), _id(2)
NO_ORG = UUID("00000000-0000-0000-0000-000000000000")   # COALESCE stand-in for a platform row's NULL org
FIXTURE_ORGS = [ORG_A, ORG_B]

# users: (org, users.role, short name, rbac role, full name)
U_SUPER, U_STAFF, U_ORGADMIN, U_PUB, U_MEMBER, U_INV2, U_INV3, U_NEW, U_PLAIN, U_ORGADMIN_B = \
    (_id(110 + i) for i in range(10))
USERS = {
    U_SUPER: (ORG_A, "super_admin", "super", "super_admin", "DR2 Super Admin"),
    U_STAFF: (ORG_A, "member", "staff", "investment_staff", "DR2 Staff"),
    U_ORGADMIN: (ORG_A, "org_admin", "orgadmin", None, "DR2 Org Admin"),
    U_PUB: (ORG_A, "member", "pub", "member", "DR2 Publisher"),
    U_MEMBER: (ORG_A, "member", "member", "member", "Jane Twoentities"),
    U_INV2: (ORG_A, "member", "inv2", "member", "Ivan Approved"),
    U_INV3: (ORG_A, "member", "inv3", "member", "Ines Requested"),
    U_NEW: (ORG_A, "member", "new", "member", "Nora Newcomer"),
    U_PLAIN: (ORG_A, "member", "plain", "member", "Paul Plainmember"),
    U_ORGADMIN_B: (ORG_B, "org_admin", "orgadminb", "member", "Olga Otherfirm Admin"),
}
E_PUB, E_MEM, E_MEM_LLC, E_INV2, E_INV3, E_NEW, E_B_SPONSOR = (_id(130 + i) for i in range(7))
INVESTOR_ENTITY = {U_MEMBER: E_MEM, U_INV2: E_INV2, U_INV3: E_INV3, U_NEW: E_NEW}
D_B = _id(150)   # org B deal (seeded directly)

COUNTED = (
    "public.users", "public.organizations", "public.deals", "public.deal_documents", "public.deal_folders",
    "public.deal_folder_named", "public.deal_delegates", "public.deal_room_settings", "public.deal_agreements",
    "public.deal_signatures", "public.deal_access", "public.entity_publishers", "public.entity_publisher_users",
    "public.agreement_templates", "public.agreement_versions", "public.deal_master_ndas",
    "public.deal_master_nda_deals", "public.entities", "public.entity_addresses", "public.compliance_records",
    "public.delegate_grants", "public.member_todos", "public.audit_log", "public.config", "public.notifications",
    "public.notification_recipients", "public.roles", "public.user_roles",
)

S: dict = {}      # state shared between sections
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


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def make_pdf(label: str, pages: int) -> bytes:
    """A real, multi-page PDF fixture (reportlab), each page naming itself."""
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(612, 792))
    for i in range(1, pages + 1):
        c.setFont("Helvetica", 14)
        c.drawString(72, 700, f"DR2 {label} page {i}")
        c.showPage()
    c.save()
    return buf.getvalue()


def pdf_texts(data: bytes) -> list[str]:
    from pypdf import PdfReader

    return [re.sub(r"\s+", " ", p.extract_text() or "") for p in PdfReader(io.BytesIO(data)).pages]


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


async def fingerprint(conn, sql, *args) -> tuple[int, str]:
    row = await platform(conn, f"SELECT count(*) AS n, md5(COALESCE(string_agg(t::text, '' ORDER BY t::text), '')) "
                               f"AS s FROM ({sql}) t", *args, fetch="fetchrow")
    return int(row["n"]), row["s"]


async def real_fingerprints(conn) -> dict:
    """What this run must never touch: note_gold_values, real deals, the real
    signature chain, real (non-fixture) templates."""
    return {
        "note_gold_values": await fingerprint(conn, "SELECT * FROM portfolio.note_gold_values"),
        "deals": await fingerprint(conn, "SELECT id, name, system_from, system_to, valid_to FROM deals "
                                         "WHERE NOT (org_id = ANY($1::uuid[]))", FIXTURE_ORGS),
        "platform_signatures": await fingerprint(conn, "SELECT id, record_sha256 FROM deal_signatures "
                                                       "WHERE chain = 'platform'"),
        "real_templates": await fingerprint(conn, "SELECT id, key, version, status, body, fields FROM "
                                                  "agreement_templates WHERE NOT (COALESCE(org_id, $1) = ANY($2::uuid[]))"
                                                  " AND key NOT LIKE $3", NO_ORG, FIXTURE_ORGS, f"{TAG}%"),
    }


# DEALROOM3 — the teardown moved onto the shared scoped helper
# (scripts/_verify_cleanup.py, lotmethod/BUGFIX2): ONE transaction on the
# operator DSN with the immutability GUCs set LOCAL, keyed only by this run's
# fixture ids, FK closure over materialised values, a refusal if any row
# outside the fixture orgs would be reached. Replaces fk_children /
# delete_tree / purge_privileged (the recursive FK walk). No assertion changed.
async def operator_conn():
    dsn, how = await resolve_dsn(OPERATOR_DSN_VAR)
    if not dsn:
        raise RuntimeError(f"no operator DSN for the fixture teardown path: {how}")
    return await connect(dsn)


async def teardown(conn) -> list[str]:
    users = list(USERS)
    op = await operator_conn()
    try:
        real_refs = await op.fetchval(
            "SELECT count(*) FROM deals WHERE NOT (org_id = ANY($1::uuid[])) AND (created_by = ANY($2::uuid[]) "
            "OR submitted_by = ANY($2::uuid[]) OR reviewed_by = ANY($2::uuid[]))", FIXTURE_ORGS, users)
        if real_refs:
            raise RuntimeError(f"{real_refs} real deal(s) reference a fixture user — refusing to delete it")
        real_tpl = await op.fetchval(
            "SELECT count(*) FROM agreement_templates WHERE (created_by = ANY($1::uuid[]) OR published_by = "
            "ANY($1::uuid[])) AND NOT (COALESCE(org_id, $2) = ANY($3::uuid[])) AND key NOT LIKE $4",
            users, NO_ORG, FIXTURE_ORGS, f"{TAG}%")
        if real_tpl:
            raise RuntimeError(f"{real_tpl} real template(s) reference a fixture user — refusing to delete it")
        org_or_user = ("org_id = ANY($1::uuid[]) OR user_id = ANY($2::uuid[])", [FIXTURE_ORGS, users])
        plan = {"public.audit_log": org_or_user, "public.member_todos": org_or_user,
                "public.agreement_templates": ("org_id = ANY($1::uuid[]) OR (org_id IS NULL AND key LIKE $2)",
                                               [FIXTURE_ORGS, f"{TAG}%"])}
        res = await VC.cleanup(op, orgs=FIXTURE_ORGS, users=users, user_sub_like=f"%{TAG}%", plan=plan,
                               gucs=("dealroom.signature_teardown", "dealroom.activity_teardown"),
                               label="verify_dealroom2")
        return res["log"]
    finally:
        await op.close()


async def leftovers(conn) -> int:
    n = 0
    for sql, args in (
        ("SELECT count(*) FROM users WHERE id = ANY($1::uuid[]) OR auth0_sub LIKE $2", [list(USERS), f"%{TAG}%"]),
        ("SELECT count(*) FROM organizations WHERE id = ANY($1::uuid[])", [FIXTURE_ORGS]),
        ("SELECT count(*) FROM deals WHERE org_id = ANY($1::uuid[])", [FIXTURE_ORGS]),
        ("SELECT count(*) FROM deal_signatures WHERE org_id = ANY($1::uuid[])", [FIXTURE_ORGS]),
        ("SELECT count(*) FROM agreement_versions WHERE org_id = ANY($1::uuid[])", [FIXTURE_ORGS]),
        ("SELECT count(*) FROM deal_master_ndas WHERE org_id = ANY($1::uuid[])", [FIXTURE_ORGS]),
        ("SELECT count(*) FROM agreement_templates WHERE org_id = ANY($1::uuid[]) OR key LIKE $2",
         [FIXTURE_ORGS, f"{TAG}%"]),
        ("SELECT count(*) FROM deal_access WHERE org_id = ANY($1::uuid[])", [FIXTURE_ORGS]),
        ("SELECT count(*) FROM entities WHERE org_id = ANY($1::uuid[])", [FIXTURE_ORGS]),
        ("SELECT count(*) FROM config WHERE org_id = ANY($1::uuid[])", [FIXTURE_ORGS]),
        ("SELECT count(*) FROM audit_log WHERE org_id = ANY($1::uuid[])", [FIXTURE_ORGS]),
        ("SELECT count(*) FROM notifications WHERE org_id = ANY($1::uuid[])", [FIXTURE_ORGS]),
    ):
        n += int(await platform(conn, sql, *args, fetch="fetchval"))
    return n


# ═══ Seed ═══════════════════════════════════════════════════════════════════
SEEDED_TABLES = ("public.users", "public.entities", "public.delegate_grants", "public.compliance_records",
                 "public.entity_addresses", "public.deals", "public.config", "public.user_roles",
                 "public.agreement_templates", "public.agreement_versions", "public.deal_agreements",
                 "public.deal_signatures", "public.deal_access", "public.deal_master_ndas",
                 "public.deal_master_nda_deals", "public.deal_documents")


async def y_constraints(conn):
    section("[Y] Seed constraints — every UNIQUE (incl. partial), CHECK and FK read from the catalog first")
    rows = await conn.fetch(
        """
        SELECT conrelid::regclass::text AS t, conname, contype::text AS k, pg_get_constraintdef(oid) AS d
        FROM pg_constraint WHERE conrelid = ANY($1::regclass[]) AND contype IN ('u', 'c', 'x', 'f')
        UNION ALL
        SELECT indrelid::regclass::text, indexrelid::regclass::text, 'i', pg_get_indexdef(indexrelid) FROM pg_index
        WHERE indrelid = ANY($1::regclass[]) AND indisunique AND NOT indisprimary
        """, list(SEEDED_TABLES))
    for r in rows:
        print(f"[INFO] {r['t']}: {r['conname']} ({r['k']}) — {r['d'][:150]}")
    have = {r["conname"]: r["d"] for r in rows}
    check("compliance_records_entity_id_key" in have,
          "compliance_records is UNIQUE per entity — one row per seeded entity", f"{len(rows)} read")
    check(any(n.startswith("users_") and "(email)" in d for n, d in have.items()),
          "users.email is UNIQUE — every fixture email carries the run tag")
    check("agreement_versions_agreement_version_uq" in have and "deal_master_ndas_live_uq" in have
          and "WHERE (revoked_at IS NULL)" in have["deal_master_ndas_live_uq"],
          "Phase 2 uniques read: one version number per agreement; ONE live master NDA per publisher (partial)")
    check("agreement_templates_key_version_uq" in have,
          "templates are unique on (key, version, org) — the run's keys carry the tag / live in fixture orgs")
    check("deal_signatures_record_version_check" in have and "deal_access_status_v2_check" in have
          and "deal_agreements_kind_shape_check" in have,
          "Phase 2 CHECKs read: record_version shape, resign_required status, template-vs-upload shape")
    check("deal_access_prior_level_check" in have and "resign_required" in have["deal_access_prior_level_check"],
          "prior_level only on a resign_required row (CHECK)")


async def seed(conn) -> None:
    from services import rbac

    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        for org, nm in ((ORG_A, "a"), (ORG_B, "b")):
            await conn.execute("INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3)",
                               org, f"DEALROOM2 Verify Org {nm.upper()}", f"{TAG}-{nm}")
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
        await rbac.grant_org_admin(conn, U_ORGADMIN_B, ORG_B)

        await conn.execute("INSERT INTO entities (id, org_id, entity_type, display_name, legal_name) "
                           "VALUES ($1, $2, 'llc', 'DR2 Acme Advisors LLC', 'DR2 Acme Advisors LLC')", E_PUB, ORG_A)
        await conn.execute("INSERT INTO entity_addresses (org_id, entity_id, street1, city, state, country, "
                           "is_primary) VALUES ($1, $2, '1 Main St', 'Wilmington', 'DE', 'US', true)", ORG_A, E_PUB)
        investing = [(uid, eid, "individual", USERS[uid][4]) for uid, eid in INVESTOR_ENTITY.items()]
        investing.append((U_MEMBER, E_MEM_LLC, "llc", "DR2 Twoentities Family LLC"))
        for uid, eid, etype, name in investing:
            org = USERS[uid][0]
            await conn.execute("INSERT INTO entities (id, org_id, entity_type, display_name, legal_name) "
                               "VALUES ($1, $2, $3::entity_type, $4, $4)", eid, org, etype, name)
            await conn.execute("INSERT INTO entity_addresses (org_id, entity_id, street1, city, state, country, "
                               "is_primary) VALUES ($1, $2, '9 Elm St', 'Boston', 'MA', 'US', true)", org, eid)
            await conn.execute("INSERT INTO delegate_grants (org_id, principal_entity_id, delegate_user_id, scope) "
                               "VALUES ($1, $2, $3, 'transact')", org, eid, uid)
            await conn.execute(
                "INSERT INTO compliance_records (org_id, entity_id, kyc_status, ofac_screen_status, "
                "accreditation_status, accreditation_basis, accreditation_verified_date) "
                "VALUES ($1, $2, 'approved'::kyc_status, 'passed'::ofac_status, "
                "'third_party_verified'::accreditation_status, 'income', current_date - 30)", org, eid)
        await conn.execute("INSERT INTO entities (id, org_id, entity_type, display_name) VALUES ($1, $2, "
                           "'corporation', 'DR2 Org B Sponsor')", E_B_SPONSOR, ORG_B)
        await conn.execute("INSERT INTO deals (id, org_id, name, slug, deal_status, publisher_entity_id) "
                           "VALUES ($1, $2, 'DR2 Org B Deal', $3, 'active', $4)", D_B, ORG_B, f"{TAG}-b",
                           E_B_SPONSOR)
    from services import deal_room as R

    async with conn.transaction():
        await conn.execute("SELECT set_config('app.current_org_id', $1, true), "
                           "set_config('app.is_super_admin', 'false', true)", str(ORG_B))
        await R.init_deal_room(conn, ORG_B, D_B)


# ═══ The real app, token stubbed by bearer string ══════════════════════════
CLAIMS_BY_TOKEN: dict[str, dict] = {}
R2_STORE: dict = {}


def token_for(uid: UUID) -> str:
    c = {"sub": sub_of(uid), "org_id": str(USERS[uid][0])}
    key = "t" + str(abs(hash(json.dumps(c, sort_keys=True))))
    CLAIMS_BY_TOKEN[key] = c
    return key


async def _request(uid, method, path, body=None, files=None, data=None, timeout=120.0):
    import main

    kw = {"headers": {"Authorization": f"Bearer {token_for(uid)}", "User-Agent": "verify_dealroom2"}}
    if body is not None:
        kw["json"] = body
    if files is not None:
        kw["files"] = files
    if data is not None:
        kw["data"] = data
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app, raise_app_exceptions=False),
                                 base_url="http://verify", timeout=timeout) as client:
        try:
            return await asyncio.wait_for(client.request(method, path, **kw), timeout)
        except asyncio.TimeoutError:
            return None


async def api(uid, method, path, body=None, files=None, data=None, timeout=120.0):
    res = await _request(uid, method, path, body, files, data, timeout)
    if res is None:
        return 599, {"timeout": True}
    try:
        parsed = json.loads(res.text)
    except ValueError:
        parsed = {"raw": res.text}
    return res.status_code, parsed


async def api_raw(uid, method, path):
    res = await _request(uid, method, path)
    if res is None:
        return 599, b"", {}
    return res.status_code, res.content, dict(res.headers)


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
        R2_STORE[(bucket, key)] = bytes(data)
        return key

    def fake_download(key, bucket=None):
        return R2_STORE[(bucket, key)]

    storage.get_s3_client = no_r2
    storage.delete_object = no_r2
    storage.upload_bytes = fake_upload
    storage.download_bytes = fake_download
    storage.get_signed_url = lambda key, expires=3600, bucket=None: f"https://r2.mock/{bucket}/{key}?exp={expires}"


async def room(uid, deal):
    return await api(uid, "GET", f"/api/v1/deal-room/deals/{deal}")


def file_names(envelope) -> set:
    return {d["file_name"] for f in (envelope or {}).get("folders", []) for d in f.get("documents", [])}


async def request(uid, deal, entity=None):
    return await api(uid, "POST", f"/api/v1/deal-room/deals/{deal}/access/request",
                     {"entity_id": str(entity or INVESTOR_ENTITY[uid])})


async def render(uid, deal, aid):
    return await api(uid, "GET", f"/api/v1/deal-room/deals/{deal}/agreements/{aid}/render")


async def sign(uid, deal, aid, **override):
    code, doc = await render(uid, deal, aid)
    body = {"typed_name": USERS[uid][4], "consent": True}
    if doc.get("kind") == "upload":
        body["file_sha256"] = doc.get("file_sha256")
    else:
        body["rendered_text_sha256"] = doc.get("rendered_text_sha256")
    body.update(override)
    c2, res = await api(uid, "POST", f"/api/v1/deal-room/deals/{deal}/agreements/{aid}/sign", body)
    return c2, res, doc


async def access_row(conn, deal, uid):
    return await scoped(conn, ORG_A, "SELECT id, status, level_granted, prior_level, resign_after FROM deal_access "
                                     "WHERE deal_id = $1 AND user_id = $2 AND valid_to IS NULL",
                        UUID(str(deal)), uid, fetch="fetchrow")


async def sig_row(conn, sig_id):
    from services import deal_room as R

    return await scoped(conn, ORG_A, f"SELECT {R.SIGNATURE_COLS} FROM deal_signatures WHERE id = $1",
                        UUID(str(sig_id)), fetch="fetchrow")


async def versions(conn, aid):
    return await scoped(conn, ORG_A, "SELECT id, version, kind, resign_policy, body, fields, field_values, "
                                     "file_sha256, page_count, template_id, md5(t::text) AS fp FROM agreement_versions t "
                                     "WHERE deal_agreement_id = $1 ORDER BY version", UUID(str(aid)))


async def publish_nda(deal, entity_type="limited liability company"):
    code, body = await api(U_PUB, "POST", f"/api/v1/deal-room/deals/{deal}/agreements",
                           {"template_id": str(S["tpl_nda"]), "field_values": {"publisher_entity_type": entity_type}})
    aid = body.get("id")
    code2, _ = await api(U_PUB, "POST", f"/api/v1/deal-room/deals/{deal}/agreements/{aid}/publish")
    return aid, code, code2


# ═══ Sections ══════════════════════════════════════════════════════════════
async def y_task1(conn):
    section("[Y] Task 1 — findings 1a–1d reported with their files; the chosen PDF library is pinned and importable")
    txt = (REPO / "docs" / "DEALROOM2_DISCOVERY.md").read_text(encoding="utf-8")
    for key, needles in {
        "1a": ("deal_agreements.template_id", "edit_agreement", "field_values", "agreement_templates", "[FIND]"),
        "1b": ("DataRoom.jsx", "render_for_signer", "rendered_text_sha256", "pypdfium2"),
        "1c": ("CANON_FIELDS", "record_version", "resign_required", "deal_room_folder_open", "prior_level"),
        "1d": ("reportlab==4.4.4", "pypdf==6.1.1", "py3-none-any", "DOCX", "PDF only"),
    }.items():
        part = re.search(rf"^## {key}\..*?(?=^## |\Z)", txt, re.S | re.M)
        body = part.group(0) if part else ""
        check(bool(part) and all(n in body for n in needles), f"{key} reported with its file(s) and evidence",
              f"missing {[n for n in needles if n not in body]}")
    req = (API_DIR / "requirements.txt").read_text(encoding="utf-8")
    import pypdf
    import reportlab

    check("reportlab==4.4.4" in req and "pypdf==6.1.1" in req and reportlab.Version == "4.4.4"
          and pypdf.__version__ == "6.1.1", "reportlab 4.4.4 + pypdf 6.1.1 pinned in requirements and importable",
          f"{reportlab.Version} / {pypdf.__version__}")


async def y_setup(conn):
    section("[Y] Setup — publisher, six fixture deals (created through the API), folder documents")
    code, _ = await api(U_STAFF, "POST", "/api/v1/deal-room/publishers", {"entity_id": str(E_PUB),
                                                                          "user_ids": [str(U_PUB)]})
    check(code == 201, "staff makes the fixture firm a publisher with its user", f"HTTP {code}")
    S["deals"] = {}
    for key, name in (("d1", "DR2 Versions"), ("d2", "DR2 Upload"), ("dm", "DR2 Master Home"),
                      ("d3", "DR2 Covered A"), ("d3b", "DR2 Covered B"), ("d4", "DR2 Outside Scope")):
        code, body = await api(U_PUB, "POST", "/api/v1/deal-room/deals", {"name": name, "description": "teaser"})
        check(code == 201 and body.get("publisher_entity_id") == str(E_PUB), f"publisher creates {name}",
              f"HTTP {code} {dumps(body)}")
        S["deals"][key] = body.get("id")
    await platform(conn, "UPDATE deals SET deal_status = 'active' WHERE id = ANY($1::uuid[]) AND system_to IS NULL",
                   [UUID(d) for d in S["deals"].values() if d], fetch="execute")
    S["tpl_nda"] = await platform(conn, "SELECT id FROM agreement_templates WHERE key = 'platform_nda' AND version = 1 "
                                        "AND org_id IS NULL", fetch="fetchval")
    check(S["tpl_nda"] is not None, "the platform NDA v1 template exists")
    S["docs"] = {}
    for key in ("d1", "d2"):
        fid = await platform(conn, "SELECT id FROM deal_folders WHERE deal_id = $1 AND key = '02_offering_memorandum' "
                                   "AND system_to IS NULL", UUID(S["deals"][key]), fetch="fetchval")
        fname = f"dr2-{key}-om.pdf"
        code, body = await api(U_PUB, "POST", f"/api/v1/deal-room/deals/{S['deals'][key]}/folders/{fid}/documents",
                               files={"file": (fname, make_pdf(fname, 1), "application/pdf")})
        check(code == 201, f"{fname} filed in 02 Offering Memorandum (after_agreements)", f"HTTP {code}")
        S["docs"][key] = body.get("id")


async def y_uploads(conn):
    section("[Y] Upload agreement — PDF in (mocked) R2 with its SHA-256; viewer pages; signature records the FILE "
            "hash; a replaced file is a new version")
    d2 = S["deals"]["d2"]
    pdf1 = make_pdf("upload-v1", 3)
    n_r2 = len(R2_STORE)
    code, body = await api(U_PUB, "POST", f"/api/v1/deal-room/deals/{d2}/agreements/upload",
                           files={"file": ("dr2-sponsor-nda.pdf", pdf1, "application/pdf")},
                           data={"title": "DR2 Sponsor NDA"})
    check(code == 201 and body.get("file_sha256") == sha(pdf1) and body.get("page_count") == 3
          and body.get("fill_in_fields") is False, "the publisher uploads a 3-page PDF agreement; SHA-256 of the bytes",
          dumps(body))
    S["a_up"] = aid = body.get("id")
    stored = [(k, v) for k, v in R2_STORE.items() if k[1].startswith(f"deals/{d2}/agreements/")]
    check(len(R2_STORE) == n_r2 + 1 and len(stored) == 1 and stored[0][1] == pdf1,
          "stored once in (mocked) R2 under deals/{deal}/agreements/…, byte-identical", f"{[k for k, _ in stored]}")
    row = await scoped(conn, ORG_A, "SELECT kind, template_id, file_sha256, status, title FROM deal_agreements "
                                    "WHERE id = $1 AND system_to IS NULL", UUID(aid), fetch="fetchrow")
    check(row is not None and row["kind"] == "upload" and row["template_id"] is None
          and row["file_sha256"] == sha(pdf1) and row["status"] == "draft",
          "re-read: deal_agreements row is kind upload, no template, file_sha256 recorded, draft",
          dumps(dict(row) if row else None))
    for fname, data in (("dr2-terms.docx", b"PK\x03\x04 docx"), ("dr2-terms.txt", b"plain text")):
        code, body = await api(U_PUB, "POST", f"/api/v1/deal-room/deals/{d2}/agreements/upload",
                               files={"file": (fname, data, "application/octet-stream")}, data={"title": "x"})
        check(code == 415 and "PDF" in dumps(body), f"{fname} refused (415): PDF only", f"HTTP {code} {dumps(body)}")
    code, _ = await api(U_PUB, "POST", f"/api/v1/deal-room/deals/{d2}/agreements/{aid}/publish")
    vs = await versions(conn, aid)
    check(code == 200 and len(vs) == 1 and vs[0]["version"] == 1 and vs[0]["kind"] == "upload"
          and vs[0]["file_sha256"] == sha(pdf1) and vs[0]["page_count"] == 3 and vs[0]["resign_policy"] == "initial",
          "publishing writes version 1: kind upload, the file hash, 3 pages", dumps([dict(v) for v in vs]))
    S["up_v1"] = vs[0]["id"] if vs else None

    code, body = await request(U_MEMBER, d2)
    check(code == 200 and body.get("status") == "requested", "the investor requests access", dumps(body))
    code, doc = await render(U_MEMBER, d2, aid)
    check(code == 200 and doc.get("kind") == "upload" and doc.get("file_sha256") == sha(pdf1)
          and doc.get("page_count") == 3 and "text" not in doc and doc.get("fill_in_fields") is False,
          "render: the investor gets the PDF's hash and page count (no text, no fill-in fields)", dumps(doc))
    pages = []
    for n in (1, 2, 3, 4):
        c, content, hdr = await api_raw(U_MEMBER, "GET", f"/api/v1/deal-room/deals/{d2}/agreements/{aid}/pages/{n}")
        pages.append((c, content[:8] == b"\x89PNG\r\n\x1a\n", hdr.get("content-type")))
    check([p[:2] for p in pages[:3]] == [(200, True)] * 3 and pages[3][0] == 404,
          "the viewer gets every page as PNG (1–3), and no page 4", f"{pages}")
    c, _, _ = await api_raw(U_INV3, "GET", f"/api/v1/deal-room/deals/{d2}/agreements/{aid}/pages/1")
    check(c == 409, "an investor who has not requested access gets no page (409)", f"HTTP {c}")
    c, _, _ = await api_raw(U_ORGADMIN_B, "GET", f"/api/v1/deal-room/deals/{d2}/agreements/{aid}/pages/1")
    check(c == 404, "another firm: 404", f"HTTP {c}")

    code, _, _ = await sign(U_MEMBER, d2, aid, file_sha256="0" * 64)
    check(code == 409, "signing with a hash that is not the file's is refused (409)", f"HTTP {code}")
    code, res, _ = await sign(U_MEMBER, d2, aid)
    check(code == 201 and res.get("complete") is True and res.get("document_sha256") == sha(pdf1)
          and res.get("version") == 1, "the investor signs the upload", dumps(res))
    sig = await sig_row(conn, res.get("signature_id")) if res.get("signature_id") else None
    check(sig is not None and sig["document_sha256"] == sha(pdf1) and sig["agreement_version_id"] == S["up_v1"]
          and sig["record_version"] == 2 and sig["template_id"] is None and sha(pdf1) in sig["rendered_text"]
          and sig["rendered_text_sha256"] == sha(sig["rendered_text"].encode("utf-8")),
          "re-read: the signature records the FILE hash and points at version 1; its statement names the hash",
          dumps(dict(sig) if sig else None))
    S["sig_upload"] = sig["id"] if sig else None
    acc = await access_row(conn, d2, U_MEMBER)
    check(acc is not None and acc["status"] == "granted", "every agreement signed → granted (automatic)",
          dumps(dict(acc) if acc else None))
    S["member_d2_access"] = acc["id"] if acc else None

    pdf2 = make_pdf("upload-v2", 2)
    code, body = await api(U_PUB, "POST", f"/api/v1/deal-room/deals/{d2}/agreements/{aid}/versions/file",
                           files={"file": ("dr2-sponsor-nda-v2.pdf", pdf2, "application/pdf")})
    check(code == 422 and "keep" in dumps(body), "replacing a signed file without choosing keep / resign is refused",
          f"HTTP {code} {dumps(body)}")
    code, body = await api(U_PUB, "POST", f"/api/v1/deal-room/deals/{d2}/agreements/{aid}/versions/file",
                           files={"file": ("dr2-sponsor-nda-v2.pdf", pdf2, "application/pdf")},
                           data={"resign_policy": "keep"})
    check(code == 201 and body.get("version") == 2 and body.get("file_sha256") == sha(pdf2)
          and body.get("affected") == [], "replacement with keep → version 2 with the new hash; nobody affected",
          dumps(body))
    vs = await versions(conn, aid)
    live = await scoped(conn, ORG_A, "SELECT file_sha256 FROM deal_agreements WHERE id = $1 AND system_to IS NULL",
                        UUID(aid), fetch="fetchval")
    sig2 = await sig_row(conn, S["sig_upload"]) if S.get("sig_upload") else None
    check([v["version"] for v in vs] == [1, 2] and vs[0]["file_sha256"] == sha(pdf1)
          and vs[1]["file_sha256"] == sha(pdf2) and live == sha(pdf2) and sig2 is not None
          and sig2["agreement_version_id"] == vs[0]["id"] and sig2["document_sha256"] == sha(pdf1),
          "re-read: v1 keeps the old hash, v2 the new; the live agreement names v2; the old signature still v1")
    acc = await access_row(conn, d2, U_MEMBER)
    check(acc is not None and acc["id"] == S["member_d2_access"] and acc["status"] == "granted",
          "keep: the existing signer's access row is untouched (same live row)")
    await request(U_INV2, d2)
    code, res, doc = await sign(U_INV2, d2, aid)
    check(code == 201 and doc.get("file_sha256") == sha(pdf2) and res.get("document_sha256") == sha(pdf2)
          and res.get("version") == 2, "a new investor reads and signs version 2 (the new file's hash)", dumps(res))


async def y_versions(conn):
    section("[Y] Versions — immutable; each signature points at its exact version and hash; keep-signers")
    from services import deal_room as R
    from services import deal_room_templates as T

    d1 = S["deals"]["d1"]
    aid, c1, c2 = await publish_nda(d1)
    S["a1"] = aid
    check(c1 == 201 and c2 == 200, "the publisher requires and publishes the platform NDA on D1", f"{c1} {c2}")
    vs = await versions(conn, aid)
    tpl = await platform(conn, "SELECT body, fields FROM agreement_templates WHERE id = $1", S["tpl_nda"],
                         fetch="fetchrow")
    check(len(vs) == 1 and vs[0]["version"] == 1 and vs[0]["resign_policy"] == "initial"
          and vs[0]["body"] == tpl["body"] and vs[0]["template_id"] == S["tpl_nda"],
          "publishing writes version 1, snapshotting the template's body")
    S["v1"] = vs[0]["id"] if vs else None
    S["v1_fp"] = vs[0]["fp"] if vs else None
    await request(U_INV3, d1)   # requests, never signs — must stay untouched by re-signing
    for uid in (U_MEMBER, U_INV2):
        await request(uid, d1)
        code, res, doc = await sign(uid, d1, aid)
        check(code == 201 and res.get("complete") is True and res.get("version") == 1,
              f"{USERS[uid][2]} signs v1", dumps(res))
        S[f"sig_v1_{USERS[uid][2]}"] = res.get("signature_id")
    inv2 = await access_row(conn, d1, U_INV2)
    code, body = await api(U_PUB, "POST", "/api/v1/deal-room/access/decide",
                           {"access_ids": [str(inv2["id"])], "action": "approve", "level": "after_approval"})
    check(code == 200 and body["results"][0]["ok"] is True, "the publisher raises inv2 to after_approval", dumps(body))
    sig = await sig_row(conn, S["sig_v1_member"])
    check(sig is not None and sig["agreement_version_id"] == S["v1"] and sig["record_version"] == 2
          and sig["document_sha256"] == sig["rendered_text_sha256"] == sha(sig["rendered_text"].encode("utf-8"))
          and R.record_sha256(dict(sig)) == sig["record_sha256"],
          "re-read: the signature names version 1 and its text hash; record_sha256 covers both")

    code, body = await api(U_PUB, "PATCH", f"/api/v1/deal-room/deals/{d1}/agreements/{aid}",
                           {"field_values": {"publisher_entity_type": "corporation"}})
    check(code == 409 and "version" in dumps(body), "a published agreement is not edited in place (409 → versions)",
          f"HTTP {code}")
    code, body = await api(U_PUB, "POST", f"/api/v1/deal-room/deals/{d1}/agreements/{aid}/versions",
                           {"field_values": {"publisher_entity_type": "Delaware limited liability company"}})
    check(code == 422 and body.get("detail", {}).get("signatures") == 2,
          "with signatures, a new version needs the publisher's choice (keep / resign)", dumps(body))
    before = {uid: (await access_row(conn, d1, uid))["id"] for uid in (U_MEMBER, U_INV2, U_INV3)}
    code, body = await api(U_PUB, "POST", f"/api/v1/deal-room/deals/{d1}/agreements/{aid}/versions",
                           {"field_values": {"publisher_entity_type": "Delaware limited liability company"},
                            "resign_policy": "keep"})
    check(code == 201 and body.get("version") == 2 and body.get("policy") == "keep" and body.get("affected") == [],
          "keep → version 2; nobody is affected", dumps(body))
    vs = await versions(conn, aid)
    check([v["version"] for v in vs] == [1, 2] and vs[0]["fp"] == S["v1_fp"]
          and json.loads(vs[1]["field_values"]).get("publisher_entity_type") == "Delaware limited liability company"
          and json.loads(vs[0]["field_values"]).get("publisher_entity_type") == "limited liability company",
          "re-read: v1 byte-identical (row fingerprint), v2 carries the new field values")
    after = {uid: (await access_row(conn, d1, uid))["id"] for uid in (U_MEMBER, U_INV2, U_INV3)}
    acc2 = await access_row(conn, d1, U_INV2)
    check(after == before and acc2["status"] == "granted" and acc2["level_granted"] == "after_approval",
          "keep-signers: every live access row is the same row; inv2 still granted after_approval")
    code, env = await room(U_MEMBER, d1)
    check(code == 200 and "dr2-d1-om.pdf" in file_names(env), "keep: the existing signer still opens 02")
    await request(U_NEW, d1)
    code, doc = await render(U_NEW, d1, aid)
    code2, res, _ = await sign(U_NEW, d1, aid)
    check(code == 200 and "Delaware limited liability company" in doc.get("text", "") and code2 == 201
          and res.get("version") == 2, "a new investor reads and signs version 2", dumps(res))
    S["sig_v2_new"] = res.get("signature_id")

    # every signature verifies against ITS OWN version: re-render the snapshot
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.current_org_id', $1, true)", str(ORG_A))
        deal = dict(await R.fetch_deal(conn, d1))
        ok, wrong = [], []
        for sid in (S["sig_v1_member"], S["sig_v1_inv2"], S["sig_v2_new"]):
            s = await conn.fetchrow(f"SELECT {R.SIGNATURE_COLS} FROM deal_signatures WHERE id = $1", UUID(str(sid)))
            v = await R.get_version(conn, s["agreement_version_id"])
            other = await R.get_version(conn, S["v1"] if v["version"] == 2 else vs[1]["id"])
            signer = dict(await R.signer_row(conn, s["user_id"]))
            texts = set()
            for day in {s["signed_at"].date(), date.today()}:
                for src in (v, other):
                    vals = await R.agreement_values(conn, deal, R.version_doc(src), entity_id=s["entity_id"],
                                                    signer=signer, signing_date=day)
                    texts.add((src["version"], T.render(R.version_doc(src), vals)))
            ok.append(any(ver == v["version"] and t == s["rendered_text"] for ver, t in texts))
            wrong.append(any(ver != v["version"] and t == s["rendered_text"] for ver, t in texts))
    check(len(ok) == 3 and all(ok) and not any(wrong),
          "each signature's text re-renders exactly from its OWN version (and not from the other)", f"{ok} {wrong}")

    upd = await scoped(conn, ORG_A, "UPDATE agreement_versions SET title = 'Forged' WHERE id = $1", S["v1"],
                       fetch="execute")
    dele = await scoped(conn, ORG_A, "DELETE FROM agreement_versions WHERE id = $1", S["v1"], fetch="execute")
    op = await operator_conn()
    try:
        errs = []
        for sql in ("UPDATE agreement_versions SET title = 'Forged' WHERE id = $1",
                    "DELETE FROM agreement_versions WHERE id = $1"):
            try:
                async with op.transaction():
                    await op.execute(sql, S["v1"])
                errs.append("no error")
            except Exception as exc:  # noqa: BLE001
                errs.append(str(exc)[:80])
    finally:
        await op.close()
    vs2 = await versions(conn, aid)
    check(upd == "UPDATE 0" and dele == "DELETE 0" and all("immutable" in e for e in errs)
          and vs2[0]["fp"] == S["v1_fp"],
          "immutable: app_service UPDATE / DELETE match zero rows (no policy); a bypass-RLS session is refused by "
          "the trigger; v1 unchanged", f"{upd} / {dele} / {errs}")


async def y_resign(conn):
    section("[Y] Require re-sign — only affected investors lose folders above public, are notified; re-signing "
            "restores the level")
    d1, d2, aid = S["deals"]["d1"], S["deals"]["d2"], S["a1"]
    inv3_before = await access_row(conn, d1, U_INV3)
    d2_before = await access_row(conn, d2, U_MEMBER)
    code, env = await room(U_MEMBER, d1)
    check(code == 200 and "dr2-d1-om.pdf" in file_names(env), "before: the signer opens 02 Offering Memorandum")
    code, body = await api(U_PUB, "POST", f"/api/v1/deal-room/deals/{d1}/agreements/{aid}/versions",
                           {"field_values": {"publisher_entity_type": "Delaware LLC"}, "resign_policy": "resign"})
    affected = {a["user_id"] for a in body.get("affected", [])}
    check(code == 201 and body.get("version") == 3 and affected == {str(U_MEMBER), str(U_INV2), str(U_NEW)},
          "resign → version 3; affected = exactly the three granted signers of D1", dumps(body))
    rows = {uid: await access_row(conn, d1, uid) for uid in (U_MEMBER, U_INV2, U_NEW)}
    check(all(r["status"] == "resign_required" for r in rows.values())
          and rows[U_INV2]["prior_level"] == "after_approval" and rows[U_MEMBER]["prior_level"] == "after_agreements"
          and all(r["level_granted"] is None for r in rows.values()),
          "re-read: each is resign_required holding the level to restore",
          dumps({USERS[u][2]: dict(r) for u, r in rows.items()}))
    inv3 = await access_row(conn, d1, U_INV3)
    d2_after = await access_row(conn, d2, U_MEMBER)
    check(inv3["id"] == inv3_before["id"] and inv3["status"] == "requested"
          and d2_after["id"] == d2_before["id"] and d2_after["status"] == "granted",
          "NOT affected: the investor who never signed (same row, still requested) and the signer's other deal")
    code, env = await room(U_MEMBER, d1)
    names = file_names(env)
    locked = [f for f in env.get("folders", []) if not f["open"]]
    c_dl, _ = await api(U_MEMBER, "GET", f"/api/v1/deal-room/deals/{d1}/documents/{S['docs']['d1']}/download")
    c_d2, _ = await api(U_MEMBER, "GET", f"/api/v1/deal-room/deals/{d2}/documents/{S['docs']['d2']}/download")
    check(code == 200 and "dr2-d1-om.pdf" not in names and c_dl == 404 and c_d2 == 200
          and all(f["effective_level"] != "public" for f in locked) and len(locked) > 0
          and any("updated agreement" in (f.get("lock_reason") or "") for f in locked),
          "folders above public close for the affected investor (download 404, lock reason says re-sign); "
          "their other deal still opens", f"{sorted(names)} {c_dl} {c_d2}")
    todos = await platform(conn, "SELECT user_id FROM member_todos WHERE source = 'deal_room' AND related_id = $1 "
                                 "AND action_params->>'kind' = 'resign' AND status = 'open'", UUID(d1))
    got = {t["user_id"] for t in todos}
    check(got == {U_MEMBER, U_INV2, U_NEW}, "notified: an open re-sign to-do for each affected investor, nobody else",
          f"{len(got)}")
    rec = await platform(conn, "SELECT r.user_id FROM notifications n JOIN notification_recipients r "
                               "ON r.notification_id = n.id WHERE n.org_id = $1 AND n.event_type = "
                               "'deal_access_resign_required' AND n.resource_id = $2", ORG_A, UUID(d1))
    check({r["user_id"] for r in rec} == {U_MEMBER, U_INV2, U_NEW},
          "notified: a deal_access_resign_required notification to exactly the affected three")

    code, grid = await api(U_PUB, "GET", f"/api/v1/deal-room/access-grid?deal_id={d1}&status=resign_required"
                                         "&page_size=200")
    want = {str(r["id"]) for r in rows.values()}
    got = {r["id"] for r in grid.get("rows", [])}
    cell = next((r["agreement_detail"].get("platform_nda") for r in grid.get("rows", [])
                 if r["investor_user_id"] == str(U_INV2)), {})
    check(code == 200 and got == want and cell.get("stale") is True and cell.get("label") == "signed v1 — v3 needed",
          "grid: the resign_required filter returns exactly those rows (equals SQL); the cell shows the stale version",
          dumps(cell))

    for uid, level in ((U_INV2, "after_approval"), (U_MEMBER, "after_agreements")):
        code, res, _ = await sign(uid, d1, aid)
        acc = await access_row(conn, d1, uid)
        check(code == 201 and res.get("version") == 3 and acc["status"] == "granted" and acc["level_granted"] == level,
              f"{USERS[uid][2]} re-signs v3 → granted at {level} again (re-read)", dumps(dict(acc)))
    code, env = await room(U_MEMBER, d1)
    check(code == 200 and "dr2-d1-om.pdf" in file_names(env), "re-signing reopens the folders")
    open_todos = await platform(conn, "SELECT count(*) FROM member_todos WHERE related_id = $1 AND action_params->>"
                                      "'kind' = 'resign' AND status = 'open' AND user_id = ANY($2::uuid[])", UUID(d1),
                                [U_MEMBER, U_INV2], fetch="fetchval")
    check(int(open_todos) == 0, "re-signing closes the re-sign to-do")

    # bulk "require re-sign" from the grid: only the selected investor
    acc_m = await access_row(conn, d1, U_MEMBER)
    acc_2 = await access_row(conn, d1, U_INV2)
    code, body = await api(U_PUB, "POST", "/api/v1/deal-room/access/decide",
                           {"access_ids": [str(acc_m["id"])], "action": "require_resign", "reason": "Please re-confirm"})
    m = await access_row(conn, d1, U_MEMBER)
    i2 = await access_row(conn, d1, U_INV2)
    check(code == 200 and m["status"] == "resign_required" and m["resign_after"] is not None
          and i2["id"] == acc_2["id"] and i2["status"] == "granted",
          "bulk require re-sign moves only the selected row (cut-off recorded); the other is the same row", dumps(body))
    code, env = await room(U_MEMBER, d1)
    a = next((x for x in env.get("agreements", []) if x["id"] == aid), {})
    check(a.get("signed_at") is None and a.get("stale") is True, "the cut-off voids the earlier signature")
    code, res, _ = await sign(U_MEMBER, d1, aid)
    m = await access_row(conn, d1, U_MEMBER)
    check(code == 201 and m["status"] == "granted" and m["level_granted"] == "after_agreements",
          "signing again after the cut-off restores after_agreements")
    code, mine = await api(U_NEW, "GET", "/api/v1/deal-room/my-access")
    row = next((r for r in mine.get("rows", []) if r["deal_id"] == d1), {})
    check(code == 200 and row.get("status") == "resign_required"
          and [t["version"] for t in row.get("to_sign", [])] == [3]
          and [s["version"] for s in row.get("signatures", [])] == [2]
          and all(s["pdf_href"].endswith("/pdf") for s in row.get("signatures", [])),
          "My deal access: the waiting investor sees what to re-sign (v3) and their v2 signature with a PDF link",
          dumps(row))


async def y_library(conn):
    section("[Y] Firm template library — org admin create / publish; member and other-firm admin refused or blind; "
            "platform templates Super Admin only")
    lib = "/api/v1/deal-room/library"
    code, env = await api(U_ORGADMIN, "GET", lib)
    check(code == 200 and env.get("permissions", {}).get("can_write") is True
          and any(r["key"] == "platform_nda" for r in env.get("rows", []))
          and all(r["can_edit"] is False for r in env.get("rows", []) if r["scope"] == "platform"),
          "org admin reads the library; platform rows are not editable for them")
    code, body = await api(U_ORGADMIN, "POST", lib, {"source_template_id": str(S["tpl_nda"]), "key": f"{TAG}_nda"})
    S["firm_v1"] = body.get("id")
    check(code == 201 and body.get("scope") == "org" and body.get("status") == "draft" and body.get("version") == 1,
          "org admin copies the platform NDA into the firm library (draft v1)", dumps(body))
    row = await scoped(conn, ORG_A, "SELECT org_id, based_on_id, body FROM agreement_templates WHERE id = $1",
                       UUID(S["firm_v1"]), fetch="fetchrow")
    check(row is not None and row["org_id"] == ORG_A and row["based_on_id"] == S["tpl_nda"],
          "re-read: the copy belongs to the firm and names its source")
    new_body = row["body"].replace("Non-Disclosure", "Firm Non-Disclosure", 1) if row else ""
    code, body = await api(U_ORGADMIN, "PATCH", f"{lib}/{S['firm_v1']}", {"title": "DR2 Firm NDA", "body": new_body})
    got = await scoped(conn, ORG_A, "SELECT title, body FROM agreement_templates WHERE id = $1", UUID(S["firm_v1"]),
                       fetch="fetchrow")
    check(code == 200 and got["title"] == "DR2 Firm NDA" and got["body"] == new_body,
          "org admin edits the draft (re-read)", f"HTTP {code}")
    code, body = await api(U_ORGADMIN, "PATCH", f"{lib}/{S['firm_v1']}", {"body": new_body + "\n{{unknown_field}}"})
    check(code == 422 and "unknown_field" in dumps(body), "an undeclared {{field}} is refused", f"HTTP {code}")

    # identical requests across callers
    req = {"title": "DR2 matrix"}
    expect = {U_PLAIN: 403, U_STAFF: 403, U_PUB: 403, U_ORGADMIN_B: 404, U_ORGADMIN: 200}
    got = {}
    for uid in expect:
        c, b = await api(uid, "PATCH", f"{lib}/{S['firm_v1']}", req)
        got[uid] = c
        if c == 403:
            check("manage_org_settings" in dumps(b), f"{USERS[uid][2]}: 403 names manage_org_settings")
    check(got == expect, "identical PATCH: member / staff / publisher 403, other firm's admin 404, org admin 200",
          f"{ {USERS[u][2]: c for u, c in got.items()} }")
    await api(U_ORGADMIN, "PATCH", f"{lib}/{S['firm_v1']}", {"title": "DR2 Firm NDA"})
    for uid in (U_PLAIN, U_PUB):
        c, b = await api(uid, "GET", lib)
        c2, _ = await api(uid, "POST", lib, {"source_template_id": str(S["tpl_nda"]), "key": f"{TAG}_x"})
        check(c == 403 and c2 == 403, f"{USERS[uid][2]}: library read and create refused (403)", f"{c} {c2}")
    c, b = await api(U_ORGADMIN_B, "GET", lib)
    check(c == 200 and not any(r["id"] == S["firm_v1"] for r in b.get("rows", [])),
          "the other firm's admin reads their own library — firm A's template is not in it")

    code, body = await api(U_PUB, "GET", "/api/v1/deal-room/templates")
    check(code == 200 and not any(t["id"] == S["firm_v1"] for t in body.get("templates", [])),
          "a DRAFT firm template is not offered to publishers")
    code, body = await api(U_ORGADMIN, "POST", f"{lib}/{S['firm_v1']}/publish")
    st = await scoped(conn, ORG_A, "SELECT status, published_by FROM agreement_templates WHERE id = $1",
                      UUID(S["firm_v1"]), fetch="fetchrow")
    check(code == 200 and st["status"] == "active" and st["published_by"] == U_ORGADMIN,
          "org admin publishes it: draft → active (re-read)")
    pubs = {}
    for uid in (U_PUB, U_PLAIN, U_ORGADMIN_B):
        c, b = await api(uid, "GET", "/api/v1/deal-room/templates")
        pubs[uid] = any(t["id"] == S["firm_v1"] for t in b.get("templates", []))
    check(pubs == {U_PUB: True, U_PLAIN: False, U_ORGADMIN_B: False},
          "offered to the firm's publisher; invisible to a member and to another firm", f"{pubs}")
    d4 = S["deals"]["d4"]
    code, body = await api(U_PUB, "POST", f"/api/v1/deal-room/deals/{d4}/agreements",
                           {"template_id": S["firm_v1"],
                            "field_values": {"publisher_entity_type": "limited liability company"}})
    S["a_firm_d4"] = body.get("id")
    check(code == 201, "the firm's publisher requires the firm template on a deal", f"HTTP {code} {dumps(body)}")
    code, _ = await api(U_ORGADMIN_B, "POST", f"/api/v1/deal-room/deals/{D_B}/agreements",
                        {"template_id": S["firm_v1"]})
    check(code == 404, "another firm's admin cannot use it on their own deal (404)", f"HTTP {code}")
    n_before = await platform(conn, "SELECT count(*) FROM agreement_templates WHERE key = $1", f"{TAG}_nda",
                              fetch="fetchval")
    code, body = await api(U_ORGADMIN, "PATCH", f"{lib}/{S['firm_v1']}", {"title": "changed after publish"})
    check(code == 409 and "new version" in dumps(body), "an active version is never edited (409 → new version)",
          f"HTTP {code}")
    code, body = await api(U_ORGADMIN, "POST", f"{lib}/{S['firm_v1']}/versions")
    S["firm_v2"] = body.get("id")
    check(code == 201 and body.get("version") == 2 and body.get("status") == "draft", "a new draft version (v2)",
          dumps(body))
    await api(U_ORGADMIN, "PATCH", f"{lib}/{S['firm_v2']}", {"title": "DR2 Firm NDA (2026)"})
    code, _ = await api(U_ORGADMIN, "POST", f"{lib}/{S['firm_v2']}/publish")
    rows = await scoped(conn, ORG_A, "SELECT version, status, title, md5(body) AS b FROM agreement_templates "
                                     "WHERE key = $1 ORDER BY version", f"{TAG}_nda")
    uses = await scoped(conn, ORG_A, "SELECT template_id FROM deal_agreements WHERE id = $1 AND system_to IS NULL",
                        UUID(S["a_firm_d4"]), fetch="fetchval") if S.get("a_firm_d4") else None
    check(code == 200 and [(r["version"], r["status"]) for r in rows] == [(1, "retired"), (2, "active")]
          and rows[0]["title"] == "DR2 Firm NDA" and int(n_before) == 1 and uses == UUID(S["firm_v1"]),
          "publishing v2 retires v1 — kept, text unchanged; the deal agreement still uses v1",
          dumps([dict(r) for r in rows]))
    try:
        async with conn.transaction():
            await conn.execute("SELECT set_config('app.current_org_id', $1, true)", str(ORG_A))
            await conn.execute("DELETE FROM agreement_templates WHERE id = $1", UUID(S["firm_v1"]))
        del_res = "deleted"
    except Exception as exc:  # noqa: BLE001
        del_res = str(exc)[:90]
    check("kept forever" in str(del_res), "a retired version cannot be deleted (trigger)", f"{del_res}")

    # platform templates: Super Admin only
    code, body = await api(U_ORGADMIN, "PATCH", f"{lib}/{S['tpl_nda']}", {"title": "hijack"})
    check(code == 403, "org admin cannot edit a platform template (403)", f"HTTP {code}")
    code, body = await api(U_ORGADMIN, "POST", lib, {"scope": "platform", "key": f"{TAG}_platform", "title": "x",
                                                      "body": "Text {{deal_name}}",
                                                      "fields": [{"key": "deal_name", "source": "deal_record"}]})
    check(code == 403, "org admin cannot create a platform template (403)", f"HTTP {code}")
    code, body = await api(U_SUPER, "POST", lib, {"scope": "platform", "key": f"{TAG}_platform", "title": "DR2 Platform",
                                                   "body": "Text for {{deal_name}}",
                                                   "fields": [{"key": "deal_name", "source": "deal_record"}]})
    S["plat_tpl"] = body.get("id")
    check(code == 201 and body.get("scope") == "platform", "Super Admin creates a platform template", dumps(body))
    code, _ = await api(U_SUPER, "PATCH", f"{lib}/{S['plat_tpl']}", {"title": "DR2 Platform (edited)"})
    code2, _ = await api(U_ORGADMIN, "PATCH", f"{lib}/{S['plat_tpl']}", {"title": "org admin edit"})
    t = await platform(conn, "SELECT title, org_id FROM agreement_templates WHERE id = $1", UUID(S["plat_tpl"]),
                       fetch="fetchrow")
    check(code == 200 and code2 == 403 and t["title"] == "DR2 Platform (edited)" and t["org_id"] is None,
          "the same PATCH: Super Admin 200, org admin 403 (re-read)", f"{code} {code2}")


async def y_master(conn):
    section("[Y] Master NDA — covered deal needs no second signature for the same entity; a different entity must "
            "sign; an out-of-scope deal needs its own NDA; grid shows coverage")
    dm, d3, d3b, d4 = (S["deals"][k] for k in ("dm", "d3", "d3b", "d4"))
    am, c1, c2 = await publish_nda(dm)
    S["a_master"] = am
    aid4 = S.get("a_firm_d4")
    code, _ = await api(U_PUB, "POST", f"/api/v1/deal-room/deals/{d4}/agreements/{aid4}/publish")
    check(c2 == 200 and code == 200, "the master's home deal publishes its NDA; D4 publishes its own (firm) NDA",
          f"{c2} {code}")
    path = f"/api/v1/deal-room/publishers/{E_PUB}/master-nda"
    code, _ = await api(U_MEMBER, "PUT", path, {"deal_agreement_id": am, "scope": "selected", "deal_ids": [d3, d3b]})
    check(code == 403, "an investor cannot set a publisher's master NDA (403)", f"HTTP {code}")
    code, _ = await api(U_ORGADMIN_B, "PUT", path, {"deal_agreement_id": am, "scope": "all"})
    check(code == 404, "another firm: 404", f"HTTP {code}")
    code, body = await api(U_PUB, "PUT", path, {"deal_agreement_id": am, "scope": "selected", "deal_ids": [d3, d3b]})
    m = body.get("master_nda") or {}
    check(code == 200 and m.get("scope") == "selected" and set(m.get("deal_ids", [])) == {d3, d3b},
          "the publisher marks it as master NDA for two selected deals", dumps(body))

    await request(U_MEMBER, dm)
    code, res, _ = await sign(U_MEMBER, dm, am)
    check(code == 201 and res.get("complete") is True, "the investor signs the master on its home deal (entity E_MEM)")
    S["sig_master"] = res.get("signature_id")
    n_before = await scoped(conn, ORG_A, "SELECT count(*) FROM deal_signatures WHERE user_id = $1", U_MEMBER,
                            fetch="fetchval")
    code, body = await request(U_MEMBER, d3)
    acc = await access_row(conn, d3, U_MEMBER)
    n_after = await scoped(conn, ORG_A, "SELECT count(*) FROM deal_signatures WHERE user_id = $1", U_MEMBER,
                           fetch="fetchval")
    check(code == 200 and body.get("status") == "granted" and acc["status"] == "granted" and n_after == n_before,
          "covered deal, same entity: granted at once with NO new signature (re-read)", f"{n_before}→{n_after}")
    code, env = await room(U_MEMBER, d3)
    ag = env.get("agreements", [])
    check(code == 200 and len(ag) == 1 and ag[0]["is_master"] is True and ag[0]["covered_by_master"] is True
          and ag[0]["signature_id"] == S["sig_master"], "the covered deal's room shows the master as covered")
    code, grid = await api(U_PUB, "GET", f"/api/v1/deal-room/access-grid?deal_id={d3}&page_size=200")
    row = next((r for r in grid.get("rows", []) if r["investor_user_id"] == str(U_MEMBER)), {})
    sig_date = (await sig_row(conn, S["sig_master"]))["signed_at"].date().isoformat()
    check(code == 200 and row.get("agreement_detail", {}).get("master_nda", {}).get("label")
          == f"covered by master NDA signed {sig_date}"
          and row.get("covered_by_master") == f"covered by master NDA signed {sig_date}",
          "grid: 'covered by master NDA signed <date>'", dumps(row.get("agreement_detail")))

    code, body = await request(U_MEMBER, d3b, entity=E_MEM_LLC)
    check(code == 200 and body.get("status") == "requested",
          "covered deal, a DIFFERENT entity of the same investor: still has to sign", dumps(body))
    code, res, _ = await sign(U_MEMBER, d3b, am)
    s = await sig_row(conn, res.get("signature_id")) if res.get("signature_id") else None
    check(code == 201 and s is not None and s["entity_id"] == E_MEM_LLC and str(s["deal_id"]) == d3b
          and str(s["deal_agreement_id"]) == am and (await access_row(conn, d3b, U_MEMBER))["status"] == "granted",
          "…signs the master for that entity on the covered deal → granted")
    code, body = await request(U_MEMBER, d4)
    code2, env = await room(U_MEMBER, d4)
    check(code == 200 and body.get("status") == "requested" and code2 == 200
          and [a["id"] for a in env.get("agreements", [])] == [aid4] and not env["agreements"][0]["is_master"],
          "a deal outside the master's scope still requires its own NDA (and lists no master)", dumps(body))
    code, res, _ = await sign(U_MEMBER, d4, aid4)
    check(code == 201 and res.get("complete") is True, "…which the investor signs separately")

    acc = await access_row(conn, d3, U_MEMBER)
    sigs_before = await fingerprint(conn, "SELECT id, record_sha256 FROM deal_signatures WHERE user_id = $1", U_MEMBER)
    code, body = await api(U_PUB, "POST", "/api/v1/deal-room/access/decide",
                           {"access_ids": [str(acc["id"])], "action": "revoke"})
    dm_acc = await access_row(conn, dm, U_MEMBER)
    d3b_acc = await access_row(conn, d3b, U_MEMBER)
    check(code == 200 and (await access_row(conn, d3, U_MEMBER))["status"] == "revoked"
          and dm_acc["status"] == "granted" and d3b_acc["status"] == "granted"
          and await fingerprint(conn, "SELECT id, record_sha256 FROM deal_signatures WHERE user_id = $1", U_MEMBER)
          == sigs_before, "revoking one covered deal leaves the master and the other deals untouched (re-read)")
    await request(U_INV2, d3)
    acc2 = await access_row(conn, d3, U_INV2)
    check(acc2["status"] == "requested", "an investor whose entity never signed the master must sign on the covered deal")


async def y_pdf(conn):
    section("[Y] Signed-copy PDF — template and upload signatures; certificate hashes equal the row; gated like HTML")
    docs_before = await platform(conn, "SELECT count(*) FROM deal_documents WHERE org_id = $1", ORG_A, fetch="fetchval")
    r2_before = len(R2_STORE)
    sid = S["sig_v1_member"]
    row = await sig_row(conn, sid)
    code, pdf, hdr = await api_raw(U_MEMBER, "GET", f"/api/v1/deal-room/signatures/{sid}/pdf")
    check(code == 200 and hdr.get("content-type", "").startswith("application/pdf") and pdf.startswith(b"%PDF-"),
          "the investor downloads the template signature as PDF", f"HTTP {code} {hdr.get('content-type')}")
    texts = pdf_texts(pdf) if code == 200 else []
    cert = texts[-1] if texts else ""
    first_line = re.sub(r"\s+", " ", row["rendered_text"].strip().splitlines()[0])[:40]
    check(len(texts) >= 2 and first_line in texts[0], "the document pages carry the signed text",
          f"{len(texts)} pages")
    want = {"record": row["record_sha256"], "document": row["document_sha256"], "text": row["rendered_text_sha256"],
            "prev": row["prev_signature_sha256"] or "first in chain", "id": str(row["id"]),
            "typed": row["typed_name"], "chain": f"test #{row['chain_seq']}", "ua": "verify_dealroom2"}
    missing = [k for k, v in want.items() if v not in cert]
    check("Certificate of electronic signature" in cert and not missing,
          "certificate page: record, document and text SHA-256, previous hash, chain position, typed name, user "
          "agent — each EQUAL to the DB row", f"missing {missing}")
    check("UTC" in cert and ("EDT" in cert or "EST" in cert) and "I agree to sign electronically" in cert
          and "Investing entity" in cert and "version 1" in cert and "/signatures/verify" in cert,
          "certificate: time in UTC and ET, the consent statement, entity, version, verify reference")
    if row["ip"]:
        check(row["ip"] in cert, "certificate: the IP address", row["ip"])

    sid = S["sig_upload"]
    row = await sig_row(conn, sid)
    code, pdf, _ = await api_raw(U_MEMBER, "GET", f"/api/v1/deal-room/signatures/{sid}/pdf")
    texts = pdf_texts(pdf) if code == 200 else []
    check(code == 200 and len(texts) == 4 and "DR2 upload-v1 page 1" in texts[0] and "DR2 upload-v1 page 3" in texts[2]
          and row["document_sha256"] in texts[-1] and row["record_sha256"] in texts[-1],
          "upload signature: the uploaded PDF's own 3 pages, then the certificate with the FILE hash and record hash",
          f"{len(texts)} pages")

    for uid, want_code in ((U_PUB, 200), (U_STAFF, 200), (U_ORGADMIN, 200), (U_SUPER, 200), (U_INV3, 404),
                           (U_PLAIN, 404), (U_ORGADMIN_B, 404)):
        c_pdf, _, _ = await api_raw(uid, "GET", f"/api/v1/deal-room/signatures/{sid}/pdf")
        c_html, _, _ = await api_raw(uid, "GET", f"/api/v1/deal-room/signatures/{sid}/copy")
        check(c_pdf == want_code and c_html == want_code,
              f"{USERS[uid][2]}: PDF and HTML copy both {want_code} (the same gate)", f"{c_pdf} / {c_html}")
    docs_after = await platform(conn, "SELECT count(*) FROM deal_documents WHERE org_id = $1", ORG_A, fetch="fetchval")
    check(docs_after == docs_before and len(R2_STORE) == r2_before,
          "generated on demand: no document row and no stored object was written")
    S["pdf_ok"] = True


async def y_chain(conn):
    section("[Y] Chain — still append-only; the verifier passes over Phase 1 and Phase 2 rows")
    from services import deal_room as R
    from services import deal_room_templates as T

    # a Phase 1-format row (record_version 1: the FIXED Phase 1 canonical record), via the same insert path
    text = "DEALROOM2 verify — a Phase 1 format signature record.\n"
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.current_org_id', $1, true), "
                           "set_config('app.is_super_admin', 'false', true)", str(ORG_A))
        from datetime import datetime, timezone

        p1 = await R.insert_signature(conn, {
            "org_id": ORG_A, "deal_id": UUID(S["deals"]["d1"]), "deal_agreement_id": UUID(S["a1"]),
            "template_id": S["tpl_nda"], "template_version": 1, "rendered_text": text,
            "rendered_text_sha256": T.text_sha256(text), "user_id": U_INV3, "entity_id": E_INV3,
            "typed_name": USERS[U_INV3][4], "consent": True, "signed_at": datetime.now(timezone.utc),
            "ip": None, "user_agent": "verify_dealroom2", "chain": "test", "record_version": 1})
    S["sig_phase1"] = p1["id"]
    row = await sig_row(conn, p1["id"])
    canon = json.loads(R.canonical_record(dict(row)))
    check(row["record_version"] == 1 and set(canon) == set(R.CANON_FIELDS)
          and R.record_sha256(dict(row)) == row["record_sha256"],
          "a record_version 1 row hashes exactly the Phase 1 field set")
    code, pdf, _ = await api_raw(U_PUB, "GET", f"/api/v1/deal-room/signatures/{p1['id']}/pdf")
    texts = pdf_texts(pdf) if code == 200 else []
    check(code == 200 and texts and row["record_sha256"] in texts[-1] and "record format v1" in texts[-1],
          "a Phase 1 signature gets its PDF the same way")

    rows = await platform(conn, f"SELECT {R.SIGNATURE_COLS} FROM deal_signatures WHERE chain = 'test' "
                                "ORDER BY chain_seq")
    v = R.verify_chain_rows(rows)
    kinds = {int(r["record_version"]) for r in rows if r["org_id"] in FIXTURE_ORGS}
    check(v["ok"] is True and kinds == {1, 2}, "the verifier passes over the test chain holding Phase 1 AND Phase 2 "
                                               "rows", dumps(v))
    code, body = await api(U_SUPER, "GET", "/api/v1/deal-room/signatures/verify?chain=test")
    check(code == 200 and body.get("ok") is True, "GET /deal-room/signatures/verify (Super Admin) agrees", dumps(body))
    plat = R.verify_chain_rows(await platform(conn, f"SELECT {R.SIGNATURE_COLS} FROM deal_signatures WHERE chain = "
                                                    "'platform' ORDER BY chain_seq"))
    check(plat["ok"] is True, "the real platform chain verifies (Phase 1 / Phase 2 hashing)", dumps(plat))

    role = await conn.fetchrow("SELECT current_user AS u, rolbypassrls FROM pg_roles WHERE rolname = current_user")
    check(role["u"] == "app_service" and role["rolbypassrls"] is False,
          "the verify connection is app_service with rolbypassrls = false", dumps(dict(role)))
    sid = UUID(str(S["sig_v1_member"]))
    upd = await scoped(conn, ORG_A, "UPDATE deal_signatures SET typed_name = 'Forged' WHERE id = $1", sid,
                       fetch="execute")
    dele = await scoped(conn, ORG_A, "DELETE FROM deal_signatures WHERE id = $1", sid, fetch="execute")
    op = await operator_conn()
    try:
        errs = []
        for sql in ("UPDATE deal_signatures SET agreement_version_id = NULL WHERE id = $1",
                    "DELETE FROM deal_signatures WHERE id = $1"):
            try:
                async with op.transaction():
                    await op.execute(sql, sid)
                errs.append("no error")
            except Exception as exc:  # noqa: BLE001
                errs.append(str(exc)[:70])
        check(upd == "UPDATE 0" and dele == "DELETE 0" and all("append-only" in e for e in errs),
              "append-only: app_service UPDATE / DELETE match zero rows; a bypass-RLS session is refused by the "
              "trigger (UPDATE and DELETE)", f"{upd} / {dele} / {errs}")
        async with op.transaction():
            await op.execute("SELECT set_config('dealroom.signature_teardown', 'fixture', true)")
            await op.execute("UPDATE deal_signatures SET document_sha256 = $2 WHERE id = $1", sid, "1" * 64)
        v2 = R.verify_chain_rows(await platform(conn, f"SELECT {R.SIGNATURE_COLS} FROM deal_signatures WHERE "
                                                      "chain = 'test' ORDER BY chain_seq"))
        async with op.transaction():
            await op.execute("SELECT set_config('dealroom.signature_teardown', 'fixture', true)")
            await op.execute("UPDATE deal_signatures SET document_sha256 = rendered_text_sha256 WHERE id = $1", sid)
    finally:
        await op.close()
    check(v2["ok"] is False and v2["first_break"]["id"] == str(sid),
          "a tampered Phase 2 document hash is flagged as the first break, by id", dumps(v2))
    v3 = R.verify_chain_rows(await platform(conn, f"SELECT {R.SIGNATURE_COLS} FROM deal_signatures WHERE chain = "
                                                  "'test' ORDER BY chain_seq"))
    check(v3["ok"] is True, "restored → the chain verifies again")


async def y_routes(conn):
    section("[Y] Every new route has a gate and is in AUTHZ1_ROUTES.json (--check CURRENT); no existing gate changed")
    before = json.loads(git_show(BEFORE_REF, "docs/AUTHZ1_ROUTES.json") or "{}")
    now = json.loads((REPO / "docs" / "AUTHZ1_ROUTES.json").read_text(encoding="utf-8"))

    def gates(d):
        return {(tuple(r.get("methods") or []), r["path"]): r for r in d.get("routes", [])}

    gb, gn = gates(before), gates(now)
    changed = [k for k in gb if k in gn and gb[k].get("after") != gn[k].get("after")]
    removed = [k for k in gb if k not in gn]
    check(len(gb) > 0 and not changed and not removed, "no existing route's gate changed or vanished",
          f"{changed[:5]} {removed[:5]}")
    # DEALROOM3 [FIND]: this count was already failing on main (rep1–rep7 and
    # DEALROOM3 added routes after this sprint); DEALROOM2's own routes are the
    # ones present at its own commit (DEALROOM2_REF) and new since BEFORE_REF.
    own = gates(json.loads(git_show(DEALROOM2_REF, "docs/AUTHZ1_ROUTES.json") or "{}"))
    new = [k for k in gn if k not in gb and k in own]
    check(len(new) == 14 and all(k[1].startswith("/api/v1/deal-room/") for k in new),
          "14 new routes, all on the /deal-room surface", f"{len(new)}: {[k[1] for k in new][:3]}")
    ungated = [k for k in new if gn[k]["after"] == "none beyond auth"]
    check(not ungated, "every new route names its gate", f"{ungated}")
    code, out, err = run_cmd([sys.executable, str(HERE / "authz1_routes.py"), "--check"], REPO, timeout=300)
    check(code == 0 and "CURRENT" in out, "authz1_routes.py --check: CURRENT", (out + err)[-200:])


async def y_front_end(conn):
    section("[Y] Front end — node tests for the investor flow, re-sign and grid; npm run build")
    code, out, err = run_cmd(["node", "--test", "tests/dealroom/"], WEB_DIR, timeout=300)
    check(code == 0 and re.search(r"^# fail 0", out, re.M) is not None, "node --test tests/dealroom passes",
          (out + err)[-300:] if code else "")
    for name in ("upload viewer: every page is listed, the no-fill-in-fields note shows, Exhibit A follows",
                 "Sign is gated on scroll-to-end for an upload (and on consent + name for everything)",
                 "the Sign button renders disabled before the reader has scrolled",
                 "re-sign: resign_required shows what changed and asks for the new version; signed lines show versions",
                 "my deal access: versions, PDF copies and what to re-sign",
                 "grid: version / master-NDA cells, resign_required in the filter, bulk Require re-sign from the "
                 "envelope",
                 "manager: versions, re-sign choice, upload and master NDA controls — only with can_write === true",
                 "library: copy / edit / publish / new version follow each row's server flags; nothing without "
                 "can_write",
                 "investor flow: request → not eligible → sign in order → awaiting / granted"):
        check(re.search(rf"^ok \d+ - {re.escape(name)}", out, re.M) is not None, f"node test: {name}")
    code, out, err = run_cmd(["node", "--test", "tests/"], WEB_DIR, timeout=600)
    check(code == 0 and re.search(r"^# fail 0", out, re.M) is not None, "node --test tests/ passes (0 fail)",
          (out + err)[-300:] if code else "")
    code, out, err = run_cmd(["npm", "run", "build"], WEB_DIR, timeout=1200)
    check(code == 0, "npm run build exits 0", (out + err)[-400:] if code else "")


def regression():
    section("[Y] Regression — verify_dealroom1 and verify_authz1 ONCE each, flat subprocesses AFTER teardown, "
            "VERIFY_NESTED=1")
    env = os.environ | {"VERIFY_NESTED": "1"}
    for script in ("verify_dealroom1.py", "verify_authz1.py"):
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
    pre = real = None
    try:
        await run("pre-teardown", teardown, conn)
        pre = await counts(conn)
        real = await real_fingerprints(conn)
        await run("task 1", y_task1, conn)
        await run("constraints", y_constraints, conn)
        await run("seed", seed, conn)
        await run("setup", y_setup, conn)
        if all(S.get("deals", {}).get(k) for k in ("d1", "d2", "dm", "d3", "d3b", "d4")) and S.get("tpl_nda"):
            await run("uploads", y_uploads, conn)
            await run("versions", y_versions, conn)
            if S.get("a1"):
                await run("re-sign", y_resign, conn)
            await run("library", y_library, conn)
            await run("master NDA", y_master, conn)
            if S.get("sig_v1_member") and S.get("sig_upload"):
                await run("pdf", y_pdf, conn)
                await run("chain", y_chain, conn)
            else:
                check(False, "signatures exist for the PDF and chain sections", "both sections skipped")
        else:
            check(False, "setup created the fixture deals", "every deal-scoped section skipped")
        await run("routes", y_routes, conn)
        await run("front end", y_front_end, conn)
    finally:
        section("[Y] Teardown — zero fixture rows; every touched table at its pre-test count; real rows unchanged")
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
                after = await real_fingerprints(conn)
                for k in real:
                    check(after[k] == real[k], f"real {k} unchanged (count + checksum)", f"{real[k][0]} rows")
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
