"""verify_dealroom3 — Marketplace Deal Room, Phase 3 (DEALROOM3): control and insight.

WRITTEN, NOT RUN by the sprint. The operator runs:

    doppler run -- apps/api/venv/bin/python apps/api/scripts/verify_dealroom3.py 2>&1 | grep -E "^\\[FAIL\\]|^TOTAL"
    doppler run -- apps/api/venv/bin/python apps/api/scripts/verify_dealroom3.py --cleanup-only

Pass/fail only, no prompts. Prints 'TOTAL: N PASS, M FAIL' and exits non-zero
on any failure. AUTHZ_TEST_MODE=1 is set before anything imports ``main``;
secrets are hydrated from Doppler over HTTPS (``_db_connect``) first. NO
network: R2 is mocked (``upload_bytes`` keeps the bytes in memory,
``download_bytes`` reads them back, ``get_signed_url`` returns a fake URL and
COUNTS its calls, ``get_s3_client`` / ``delete_object`` fail the run), and the
invite email (``services.invites.send_invite_email``) is stubbed.

Everything after the bearer token is the real app (middleware, Principal,
every gate, every handler, RLS) against the live database. The script's own
connection is DATABASE_URL = app_service (rolbypassrls = false, asserted);
every read sets its RLS context. Dates are computed at the call.

FIXTURES: two fixture orgs (99000000-…-0000000d3001 / d3002, is_test = true,
asserted); every fixture user carries an @test.local email (users.is_test by
trigger) and the run tag in its auth0_sub, so every signature lands in the
'test' chain. Every deal, document, access row, question, advisor and log row
is the run's own.

TEARDOWN (start and end): scripts/_verify_cleanup.py — one scoped transaction
on the operator DSN with dealroom.signature_teardown and
dealroom.activity_teardown = 'fixture' (the only path past the append-only /
immutability triggers, and only for is_test orgs / the test chain), keyed by
this run's fixture ids. Then every counted table is re-read for its exact
pre-test count and the real rows (deals, documents, access, signatures,
deal-room notifications, users, the activity log) for count + checksum.

The regression (verify_dealroom2.py, ONCE, flat, after teardown,
VERIFY_NESTED=1, 2700 s) is skipped with one '[SKIP] nested run' line when
this script itself runs nested.
"""

from __future__ import annotations

import os as _authz_os
_authz_os.environ["AUTHZ_TEST_MODE"] = "1"  # AUTHZ1: set before main is imported

import asyncio
import csv
import hashlib
import io
import json
import os
import pathlib
import re
import subprocess
import sys
import time
import traceback
from datetime import date, datetime, timedelta, timezone
from uuid import UUID

HERE = pathlib.Path(__file__).resolve().parent
API_DIR = HERE.parent
REPO = API_DIR.parents[1]
WEB_DIR = REPO / "apps" / "web"
sys.path.insert(0, str(HERE))

from _db_connect import admin_dsn, connect, hydrate, resolve_dsn  # noqa: E402

hydrate()  # BEFORE main is imported (Settings is lru_cached)

import httpx  # noqa: E402

import _authz1_common as AC  # noqa: E402
import _verify_cleanup as VC  # noqa: E402

BEFORE_REF = "376f8ef"   # dealroom3-work base — main after "merge lotmethod"
TAG = "dealroom3verify"
PREFIX = "99000000-0000-0000-0000-0000000d3"
OPERATOR_DSN_VAR = "DATABASE_URL_PRECUTOVER_POSTGRES_BACKUP"
TEARDOWN_GUCS = ("dealroom.signature_teardown", "dealroom.activity_teardown")


def _id(n: int) -> UUID:
    return UUID(f"{PREFIX}{n:03d}")


ORG_A, ORG_B = _id(1), _id(2)
FIXTURE_ORGS = [ORG_A, ORG_B]

# users: (org, users.role, short name, rbac role, full name)
U_SUPER, U_STAFF, U_PUB, INV1, INV2, INV3, INV4, U_ADV, U_PLAIN, U_B_ADMIN, U_B_INV = (_id(110 + i) for i in range(11))
USERS = {
    U_SUPER: (ORG_A, "super_admin", "super", "super_admin", "DR3 Super Admin"),
    U_STAFF: (ORG_A, "member", "staff", "investment_staff", "DR3 Staff"),
    U_PUB: (ORG_A, "member", "pub", "member", "DR3 Publisher"),
    INV1: (ORG_A, "member", "inv1", "member", "Ivy Firstinvestor"),
    INV2: (ORG_A, "member", "inv2", "member", "Ian Selfcertified"),
    INV3: (ORG_A, "member", "inv3", "member", "Ines Lapsing"),
    INV4: (ORG_A, "member", "inv4", "member", "Nate Newrelation"),
    U_ADV: (ORG_A, "member", "adv", None, "Alex Advisor"),          # no RBAC role: only the advisor row opens anything
    U_PLAIN: (ORG_A, "member", "plain", "member", "Paul Plainmember"),
    U_B_ADMIN: (ORG_B, "org_admin", "badmin", "member", "Olga Otherfirm"),
    U_B_INV: (ORG_B, "member", "binv", "member", "Bea Otherfirm"),
}
E_PUB = _id(130)
ENTITY = {INV1: _id(131), INV2: _id(132), INV3: _id(133), INV4: _id(134), U_B_INV: _id(135)}
ENTITY_NAME = {INV1: "DR3 Firstinvestor Family LLC", INV2: "Ian Selfcertified", INV3: "Ines Lapsing",
               INV4: "Nate Newrelation", U_B_INV: "Bea Otherfirm"}
ACCREDITATION = {INV1: "third_party_verified", INV2: "self_certified", INV3: "third_party_verified",
                 INV4: "third_party_verified", U_B_INV: "third_party_verified"}
D_B = _id(150)   # org B deal (seeded directly)
NEW_ADVISOR_EMAIL = f"{TAG}-newadvisor@test.local"

COUNTED = (
    "public.users", "public.organizations", "public.deals", "public.deal_documents", "public.deal_folders",
    "public.deal_room_settings", "public.deal_agreements", "public.deal_signatures", "public.deal_access",
    "public.entity_publishers", "public.entity_publisher_users", "public.agreement_versions", "public.entities",
    "public.entity_addresses", "public.compliance_records", "public.delegate_grants", "public.member_todos",
    "public.config", "public.roles", "public.user_roles", "public.deal_room_activity", "public.deal_room_watermarks",
    "public.deal_room_questions", "public.deal_room_answers", "public.deal_room_advisors",
    "public.deal_room_return_notices", "public.crm_relationship_dates",
)

S: dict = {}      # state shared between sections
SIGNED_URL_CALLS: list = []
INVITE_EMAILS: list = []
R2_STORE: dict = {}
CLAIMS_BY_TOKEN: dict[str, dict] = {}
_n_pass = 0
_n_fail = 0


def check(passed: bool, label: str, detail: str = "") -> bool:
    assert isinstance(passed, bool), f"check() got {passed!r} ({type(passed).__name__}) for {label!r}"
    global _n_pass, _n_fail
    if passed:
        _n_pass += 1
        print(f"[PASS] {label}")
    else:
        _n_fail += 1
        print(f"[FAIL] {label}" + (f" — {detail}" if detail else ""))
    return passed


def find(label: str, detail: str = "") -> None:
    print(f"[FIND] {label}" + (f" — {detail}" if detail else ""))


def section(title: str) -> None:
    print(f"\n=== {title} ===", flush=True)


def run_cmd(args, cwd, timeout=600, env=None):
    try:
        p = subprocess.run(args, cwd=str(cwd), capture_output=True, text=True, timeout=timeout, env=env)
        return p.returncode, p.stdout, p.stderr
    except subprocess.TimeoutExpired as exc:
        return 124, exc.stdout or "", f"timeout after {timeout}s"


def git_show(ref: str, path: str) -> str:
    code, out, _ = run_cmd(["git", "show", f"{ref}:{path}"], REPO, timeout=60)
    return out if code == 0 else ""


def sub_of(uid: UUID) -> str:
    return f"auth0|{TAG}-{USERS[uid][2]}"


def email_of(uid: UUID) -> str:
    return f"{TAG}-{USERS[uid][2]}@test.local"


def dumps(x) -> str:
    return json.dumps(x, ensure_ascii=False, default=str)[:500]


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def today() -> date:
    return date.today()


def make_pdf(label: str, pages: int) -> bytes:
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(612, 792))
    for i in range(1, pages + 1):
        c.setFont("Helvetica", 14)
        c.drawString(72, 700, f"DR3 {label} page {i}")
        c.showPage()
    c.save()
    return buf.getvalue()


def make_png() -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (320, 200), (250, 249, 246)).save(buf, "PNG")
    return buf.getvalue()


def pdf_texts(data: bytes) -> list[str]:
    from pypdf import PdfReader

    return [re.sub(r"\s+", " ", p.extract_text() or "") for p in PdfReader(io.BytesIO(data)).pages]


# ═══ DB helpers (app_service; RLS context explicit) ═════════════════════════
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
    """Rows this run must never touch (count + checksum before and after)."""
    o = FIXTURE_ORGS
    return {
        "deals": await fingerprint(conn, "SELECT id, name, deal_status, system_from, system_to, valid_to FROM deals "
                                         "WHERE NOT (org_id = ANY($1::uuid[]))", o),
        "deal_documents": await fingerprint(conn, "SELECT id, folder_id, deleted_at, share_unwatermarked FROM "
                                                  "deal_documents WHERE NOT (org_id = ANY($1::uuid[]))", o),
        "deal_access": await fingerprint(conn, "SELECT id, status, level_granted, valid_to, access_ends_on FROM "
                                               "deal_access WHERE NOT (org_id = ANY($1::uuid[]))", o),
        "deal_room_settings": await fingerprint(conn, "SELECT id, watermark, downloads, valid_to FROM deal_room_settings "
                                                      "WHERE NOT (org_id = ANY($1::uuid[]))", o),
        "platform_signatures": await fingerprint(conn, "SELECT id, record_sha256 FROM deal_signatures "
                                                       "WHERE chain = 'platform'"),
        "deal_room_notifications": await fingerprint(
            conn, "SELECT id FROM notifications WHERE NOT (org_id = ANY($1::uuid[])) AND (event_type LIKE 'deal\\_room%' "
                  "OR event_type LIKE 'deal\\_access%')", o),
        "users": await fingerprint(conn, "SELECT id, email, status, org_id FROM users WHERE NOT (org_id = ANY($1::uuid[]))",
                                   o),
        "deal_room_activity": await fingerprint(conn, "SELECT id FROM deal_room_activity WHERE NOT (org_id = "
                                                      "ANY($1::uuid[]))", o),
    }


async def operator_conn():
    dsn, how = await resolve_dsn(OPERATOR_DSN_VAR)
    if not dsn:
        raise RuntimeError(f"no operator DSN for the fixture teardown path: {how}")
    return await connect(dsn)


async def teardown(conn=None) -> list[str]:
    """The shared scoped helper; every key is THIS run's fixture ids."""
    users = list(USERS)
    op = await operator_conn()
    try:
        bad = await op.fetchval("SELECT count(*) FROM organizations WHERE id = ANY($1::uuid[]) AND NOT is_test",
                                FIXTURE_ORGS)
        if bad:
            raise RuntimeError(f"{bad} fixture org(s) are not is_test — refusing to clean up")
        org_or_user = ("org_id = ANY($1::uuid[]) OR user_id = ANY($2::uuid[])", [FIXTURE_ORGS, users])
        plan = {"public.audit_log": org_or_user, "public.member_todos": org_or_user}
        res = await VC.cleanup(op, orgs=FIXTURE_ORGS, users=users, user_sub_like=f"%{TAG}%", plan=plan,
                               gucs=TEARDOWN_GUCS, label="verify_dealroom3")
        return res["log"]
    finally:
        await op.close()


async def leftovers(conn=None) -> int:
    op = await operator_conn()
    try:
        return await VC.leftover_count(op, orgs=FIXTURE_ORGS, users=list(USERS), user_sub_like=f"%{TAG}%", extra=(
            ("SELECT count(*) FROM users WHERE email LIKE $1", [f"{TAG}%"]),))
    finally:
        await op.close()


# ═══ The real app, token stubbed by bearer string ══════════════════════════
def token_for(uid) -> str:
    if uid in USERS:
        c = {"sub": sub_of(uid), "org_id": str(USERS[uid][0])}
    else:                       # an invited user with its own sub (S["new_advisor_sub"])
        c = {"sub": S["subs"][uid], "org_id": str(ORG_A)}
    key = "t" + hashlib.sha256(json.dumps(c, sort_keys=True).encode()).hexdigest()[:24]
    CLAIMS_BY_TOKEN[key] = c
    return key


async def _request(uid, method, path, body=None, files=None, data=None, timeout=180.0):
    import main

    kw = {"headers": {"Authorization": f"Bearer {token_for(uid)}", "User-Agent": "verify_dealroom3"}}
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


async def api(uid, method, path, body=None, files=None, data=None):
    res = await _request(uid, method, path, body, files, data)
    if res is None:
        return 599, {"timeout": True}
    try:
        parsed = json.loads(res.text)
    except ValueError:
        parsed = {"raw": res.text[:300]}
    return res.status_code, parsed


async def api_raw(uid, method, path):
    res = await _request(uid, method, path)
    if res is None:
        return 599, b"", {}
    return res.status_code, res.content, dict(res.headers)


class _Delivery:
    def as_dict(self):
        return {"status": "skipped", "reason": "verify stub"}


def install_stubs():
    import main
    from services import invites
    from services import storage
    from services import users as users_mod
    from services.brief_blocks import register_brief_blocks

    main.verify_token = lambda tok: dict(CLAIMS_BY_TOKEN[tok])
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

    def fake_signed(key, expires=3600, bucket=None):
        SIGNED_URL_CALLS.append(key)
        return f"https://r2.mock/{bucket}/{key}?exp={expires}"

    async def fake_invite_email(conn, **kw):
        INVITE_EMAILS.append(kw.get("email"))
        return _Delivery()

    storage.get_s3_client = no_r2
    storage.delete_object = no_r2
    storage.upload_bytes = fake_upload
    storage.download_bytes = fake_download
    storage.get_signed_url = fake_signed
    invites.send_invite_email = fake_invite_email


# ═══ Small readers ═════════════════════════════════════════════════════════
async def folder_id(conn, deal, key) -> UUID:
    return await platform(conn, "SELECT id FROM deal_folders WHERE deal_id = $1 AND key = $2 AND system_to IS NULL",
                          UUID(str(deal)), key, fetch="fetchval")


async def access_row(conn, deal, uid):
    return await scoped(conn, ORG_A, "SELECT id, status, level_granted, prior_level, access_ends_on, paused_reason "
                                     "FROM deal_access WHERE deal_id = $1 AND user_id = $2 AND valid_to IS NULL",
                        UUID(str(deal)), uid, fetch="fetchrow")


async def folder_open(conn, folder, uid) -> bool:
    return bool(await scoped(conn, ORG_A, "SELECT deal_room_folder_open($1, $2)", folder, uid, fetch="fetchval"))


async def open_set(conn, deal, uid) -> set:
    rows = await scoped(conn, ORG_A, "SELECT id FROM deal_folders WHERE deal_id = $1 AND system_to IS NULL "
                                     "AND deal_room_folder_open(id, $2)", UUID(str(deal)), uid)
    return {r["id"] for r in rows}


async def now_db(conn):
    return await platform(conn, "SELECT clock_timestamp()", fetch="fetchval")


async def notif(conn, event, uid, since) -> list:
    """Recipient rows for (event, user) since ``since``, with whether an email delivery is recorded pending."""
    return await platform(conn, """
        SELECT n.id, n.payload, n.created_at,
               EXISTS (SELECT 1 FROM notification_delivery_log l WHERE l.notification_id = n.id AND l.recipient_id = r.id
                       AND l.channel = 'email' AND l.status = 'pending') AS email_pending
        FROM notifications n JOIN notification_recipients r ON r.notification_id = n.id
        WHERE n.event_type = $1 AND r.user_id = $2 AND n.created_at >= $3 AND n.org_id = ANY($4::uuid[])
        """, event, uid, since, FIXTURE_ORGS)


async def activity(conn, deal, uid=None, action=None) -> list:
    return await scoped(conn, ORG_A, "SELECT id, action, document_id, folder_id, page, watermark_id, on_behalf_of_user_id "
                                     "FROM deal_room_activity WHERE deal_id = $1 AND ($2::uuid IS NULL OR user_id = $2) "
                                     "AND ($3::text IS NULL OR action = $3) ORDER BY at",
                        UUID(str(deal)), uid, action)


async def room(uid, deal):
    return await api(uid, "GET", f"/api/v1/deal-room/deals/{deal}")


async def request(uid, deal):
    return await api(uid, "POST", f"/api/v1/deal-room/deals/{deal}/access/request", {"entity_id": str(ENTITY[uid])})


async def upload(deal, folder_key, name, data, ctype):
    fid = await folder_id(S["conn"], deal, folder_key)
    code, body = await api(U_PUB, "POST", f"/api/v1/deal-room/deals/{deal}/folders/{fid}/documents",
                           files={"file": (name, data, ctype)})
    return code, body


async def settings_put(deal, body, uid=None):
    return await api(uid or U_PUB, "PUT", f"/api/v1/deal-room/deals/{deal}/settings", body)


def docs_in(envelope) -> dict:
    return {d["file_name"]: d for f in (envelope or {}).get("folders", []) for d in f.get("documents", [])}


async def grid_row(deal, uid):
    code, g = await api(U_PUB, "GET", f"/api/v1/deal-room/access-grid?deal_id={deal}&page_size=200")
    return next((r for r in g.get("rows", []) if r["investor_user_id"] == str(uid)), None) if code == 200 else None


async def decide(uid, access_id, action, **kw):
    return await api(uid, "POST", "/api/v1/deal-room/access/decide", {"access_ids": [str(access_id)], "action": action,
                                                                       **kw})


async def sweep(today_: date, *, include_test_orgs=True):
    from services import deal_room_control as C
    from services.database import get_pool, reset_rls_context, set_rls_context

    pool = await get_pool()
    tokens = set_rls_context(str(ORG_A), False)
    try:
        return await C.org_access_sweep(pool, org_id=ORG_A, today=today_, include_test_orgs=include_test_orgs)
    finally:
        reset_rls_context(tokens)


async def compliance(entity, body):
    return await api(U_SUPER, "PUT", f"/api/v1/entities/{entity}/compliance", body)


async def search(uid, q):
    return await api(uid, "GET", f"/api/v1/document-search?q={q}")


# ═══ Sections ══════════════════════════════════════════════════════════════
async def y_task1(conn):
    section("[Y] Task 1 — findings 1a–1e reported with their evidence (docs/DEALROOM3_DISCOVERY.md)")
    txt = (REPO / "docs" / "DEALROOM3_DISCOVERY.md").read_text(encoding="utf-8")
    for key, needles in {
        "1a": ("download_room_document", "get_signed_url", "Document Search", "agreement_page",
               "signed_copy_pdf", "NotificationBus", "no attachment column", "document_policy"),
        "1b": ("pypdfium2", "0.12 s", "400 pages", "PNG / JPEG", "cannot be watermarked — ask the publisher for a PDF"),
        "1c": ("workflow_scheduler_tick.py", "not_test_org_sql", "update_compliance", "deal_access_status_v2_check",
               "expired", "paused"),
        "1d": ("create_invite", "deal's org", "deal_room_advisors", "never wider", "advisor"),
        "1e": ("crm_relationship_dates", "established_on", "created_at", "published_at", "[FIND]"),
    }.items():
        part = re.search(rf"^## {key}\..*?(?=^## |\Z)", txt, re.S | re.M)
        body = part.group(0) if part else ""
        check(bool(part) and all(n in body for n in needles), f"{key} reported with its files and evidence",
              f"missing {[n for n in needles if n not in body]}")
    import pypdfium2  # noqa: F401  (1b: present in the API process)
    from services import deal_room_watermark as W

    one = W.page_png(make_pdf("probe", 1), 0)
    check(one[:8] == b"\x89PNG\r\n\x1a\n", "1b: pypdfium2 renders a PDF page to PNG in this process")


async def y_schema(conn):
    section("[Y] Schema — landed; RLS for every operation; append-only logs; migration keeps today's behaviour")
    me = await conn.fetchrow("SELECT current_user AS u, (SELECT rolbypassrls FROM pg_roles WHERE rolname = "
                             "current_user) AS bypass")
    S["role"] = me["u"]
    check(me["bypass"] is False, "the script's connection is a non-bypassing role (rolbypassrls = false)",
          f"{me['u']} bypass={me['bypass']}")
    want = {"deal_room_advisors": 4, "deal_room_questions": 4, "deal_room_answers": 4, "deal_room_return_notices": 4,
            "crm_relationship_dates": 4, "deal_room_activity": 2, "deal_room_watermarks": 2}
    for t, n in want.items():
        rows = await conn.fetch("SELECT cmd FROM pg_policies WHERE schemaname = 'public' AND tablename = $1", t)
        cmds = sorted(r["cmd"] for r in rows)
        rls = await conn.fetchval("SELECT relrowsecurity FROM pg_class WHERE oid = to_regclass($1)", f"public.{t}")
        expect = ["DELETE", "INSERT", "SELECT", "UPDATE"] if n == 4 else ["INSERT", "SELECT"]
        check(rls is True and cmds == expect, f"{t}: RLS on, one policy per operation it takes {expect}", f"{cmds}")
    trg = {r["tgname"] for r in await conn.fetch(
        "SELECT tgname FROM pg_trigger WHERE tgrelid = ANY(ARRAY['public.deal_room_activity'::regclass, "
        "'public.deal_room_watermarks'::regclass]) AND NOT tgisinternal")}
    check({"deal_room_activity_append_only", "deal_room_watermarks_append_only"} <= trg,
          "both logs carry the append-only trigger", f"{trg}")
    dflt = await conn.fetchval("SELECT column_default FROM information_schema.columns WHERE table_name = "
                               "'deal_room_settings' AND column_name = 'watermark'")
    check("'on'" in (dflt or ""), "new deal rooms default to watermark 'on'", f"{dflt}")
    mig = json.loads((REPO / "docs" / "DEALROOM1_MIGRATION.json").read_text(encoding="utf-8"))
    ids = [UUID(d["deal_id"]) for d in mig.get("deals", [])]
    rows = await platform(conn, "SELECT deal_id, downloads, watermark, access_period, offering_type, created_by "
                                "FROM deal_room_settings WHERE deal_id = ANY($1::uuid[]) AND valid_to IS NULL", ids)
    untouched = [r for r in rows if r["created_by"] is None]
    if len(untouched) < len(rows):
        find("a manager has since changed some migrated deals' settings (those rows carry created_by)",
             f"{len(rows) - len(untouched)} of {len(rows)}")
    check(len(untouched) > 0 and all((r["downloads"], r["watermark"], r["access_period"], r["offering_type"])
                                     == ("allowed", "off", "none", "not_set") for r in untouched),
          "every migrated (pre-DEALROOM3) deal keeps downloads allowed / watermark off / no period / offering not set",
          dumps([dict(r) for r in rows if (r["downloads"], r["watermark"]) != ("allowed", "off")][:3]))
    defs = {r["conname"]: r["d"] for r in await conn.fetch(
        "SELECT conname, pg_get_constraintdef(oid) AS d FROM pg_constraint WHERE conrelid = 'public.deal_access'::regclass")}
    check("'paused'" in defs.get("deal_access_status_v2_check", "") and "'expired'" in defs.get(
        "deal_access_status_v2_check", "") and "deal_access_paused_reason_check" in defs,
          "deal_access: expired + paused statuses (CHECK name kept), paused_reason only on a paused row")


async def y_constraints(conn):
    section("[Y] Seed constraints — UNIQUE / CHECK / FK read from the catalog; every seed literal checked")
    tables = ["public.deal_room_settings", "public.deal_room_advisors", "public.deal_room_questions",
              "public.deal_room_activity", "public.deal_room_watermarks", "public.deal_access",
              "public.crm_relationship_dates", "public.compliance_records", "public.users"]
    rows = await conn.fetch(
        """
        SELECT conrelid::regclass::text AS t, conname, contype::text AS k, pg_get_constraintdef(oid) AS d
        FROM pg_constraint WHERE conrelid = ANY($1::regclass[]) AND contype IN ('u', 'c', 'f')
        UNION ALL
        SELECT indrelid::regclass::text, indexrelid::regclass::text, 'i', pg_get_indexdef(indexrelid) FROM pg_index
        WHERE indrelid = ANY($1::regclass[]) AND indisunique AND NOT indisprimary
        """, tables)
    for r in rows:
        print(f"[INFO] {r['t']}: {r['conname']} ({r['k']}) — {r['d'][:140]}")
    alltext = " ".join(r["d"] for r in rows)
    for lit in ("'view_only'", "'until_date'", "'days_after_approval'", "'rule_506b'", "'rule_506c'", "'lawyer'",
                "'first_folder_open'", "'view_page'", "'answer_read'"):
        check(lit in alltext, f"seed / request literal {lit} is allowed by a CHECK")
    check(any("deal_room_advisors_live_uq" in r["conname"] for r in rows),
          "one live advisor row per (deal, advisor) — partial UNIQUE read")
    enums = {r["t"]: set(r["v"]) for r in await conn.fetch(
        "SELECT t.typname AS t, array_agg(e.enumlabel::text) AS v FROM pg_type t JOIN pg_enum e ON e.enumtypid = t.oid "
        "WHERE t.typname = ANY($1::text[]) GROUP BY t.typname",
        ["kyc_status", "accreditation_status", "ofac_status", "entity_type", "deal_status"])}
    S["enums"] = enums
    check({"approved", "in_progress"} <= enums.get("kyc_status", set())
          and {"self_certified", "third_party_verified"} <= enums.get("accreditation_status", set())
          and "passed" in enums.get("ofac_status", set()) and {"individual", "llc", "corporation"} <=
          enums.get("entity_type", set()) and {"active", "closed"} <= enums.get("deal_status", set()),
          "every enum literal the seed / sections use exists", dumps({k: sorted(v) for k, v in enums.items()}))


async def y_prefix(conn) -> bool:
    section("[Y] Reserved fixture prefix — matches no real row")
    orgs = await platform(conn, "SELECT id, is_test FROM organizations WHERE id::text LIKE $1", f"{PREFIX}%")
    users = await platform(conn, "SELECT id, email, is_test FROM users WHERE id::text LIKE $1 OR auth0_sub LIKE $2 "
                                 "OR email LIKE $3", f"{PREFIX}%", f"%{TAG}%", f"{TAG}%")
    ok = check(all(r["is_test"] for r in orgs) and all(r["is_test"] and r["email"].endswith("@test.local")
                                                        for r in users),
               "every row under the reserved prefix / tag is a test fixture (is_test, @test.local)",
               dumps([dict(r) for r in orgs if not r["is_test"]] + [dict(r) for r in users if not r["is_test"]]))
    return ok


async def seed(conn) -> None:
    from services import deal_room as R
    from services import rbac

    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        for org, nm in ((ORG_A, "a"), (ORG_B, "b")):
            await conn.execute("INSERT INTO organizations (id, name, slug, is_test) VALUES ($1, $2, $3, true)",
                               org, f"DEALROOM3 Verify Org {nm.upper()}", f"{TAG}-{nm}")
        perm_ids = {r["name"]: r["id"] for r in await conn.fetch("SELECT id, name FROM permissions")}
        roles = {}
        for org, rname in ((ORG_A, "member"), (ORG_A, "investment_staff"), (ORG_A, "super_admin"), (ORG_B, "member")):
            desc, perms = AC.BASELINE_ROLES[rname]
            rid = await rbac.ensure_role(conn, org, rname, desc)
            for p in perms:
                if p in perm_ids:
                    await conn.execute("INSERT INTO role_permissions (role_id, permission_id) VALUES ($1, $2) "
                                       "ON CONFLICT DO NOTHING", rid, perm_ids[p])
            roles[(org, rname)] = rid
        for uid, (org, account_role, nm, rname, full) in USERS.items():
            await conn.execute(
                "INSERT INTO users (id, org_id, email, full_name, auth0_sub, role, status, is_active) "
                "VALUES ($1, $2, $3, $4, $5, $6, 'active', true)", uid, org, email_of(uid), full, sub_of(uid),
                account_role)
            if rname:
                await conn.execute("INSERT INTO user_roles (user_id, role_id) VALUES ($1, $2)", uid, roles[(org, rname)])
        await rbac.grant_org_admin(conn, U_B_ADMIN, ORG_B)
        await conn.execute("INSERT INTO entities (id, org_id, entity_type, display_name, legal_name) "
                           "VALUES ($1, $2, 'llc', 'DR3 Acme Sponsors LLC', 'DR3 Acme Sponsors LLC')", E_PUB, ORG_A)
        await conn.execute("INSERT INTO entity_addresses (org_id, entity_id, street1, city, state, country, is_primary) "
                           "VALUES ($1, $2, '1 Main St', 'Wilmington', 'DE', 'US', true)", ORG_A, E_PUB)
        for uid, eid in ENTITY.items():
            org = USERS[uid][0]
            etype = "llc" if uid == INV1 else "individual"
            await conn.execute("INSERT INTO entities (id, org_id, entity_type, display_name, legal_name) "
                               "VALUES ($1, $2, $3::entity_type, $4, $4)", eid, org, etype, ENTITY_NAME[uid])
            await conn.execute("INSERT INTO entity_addresses (org_id, entity_id, street1, city, state, country, "
                               "is_primary) VALUES ($1, $2, '9 Elm St', 'Boston', 'MA', 'US', true)", org, eid)
            await conn.execute("INSERT INTO delegate_grants (org_id, principal_entity_id, delegate_user_id, scope) "
                               "VALUES ($1, $2, $3, 'transact')", org, eid, uid)
            await conn.execute(
                "INSERT INTO compliance_records (org_id, entity_id, kyc_status, ofac_screen_status, accreditation_status, "
                "accreditation_basis, accreditation_verified_date) VALUES ($1, $2, 'approved'::kyc_status, "
                "'passed'::ofac_status, $3::accreditation_status, 'income', current_date - 30)",
                org, eid, ACCREDITATION[uid])
        await conn.execute("INSERT INTO deals (id, org_id, name, slug, deal_status) VALUES ($1, $2, 'DR3 Org B Deal', "
                           "$3, 'active')", D_B, ORG_B, f"{TAG}-b")
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.current_org_id', $1, true), "
                           "set_config('app.is_super_admin', 'false', true)", str(ORG_B))
        await R.init_deal_room(conn, ORG_B, D_B)
    flags = await platform(conn, "SELECT count(*) FILTER (WHERE is_test) AS t, count(*) AS n FROM organizations "
                                 "WHERE id = ANY($1::uuid[])", FIXTURE_ORGS, fetch="fetchrow")
    uflags = await platform(conn, "SELECT count(*) FILTER (WHERE is_test) AS t, count(*) AS n FROM users "
                                  "WHERE id = ANY($1::uuid[])", list(USERS), fetch="fetchrow")
    check(flags["t"] == flags["n"] == 2 and uflags["t"] == uflags["n"] == len(USERS),
          "every fixture org and user is is_test = true", f"orgs {dict(flags)} users {dict(uflags)}")


DEALS = (("wm", "DR3 Watermark"), ("vo", "DR3 View Only"), ("legacy", "DR3 Legacy Download"),
         ("exp", "DR3 Access Period"), ("past", "DR3 Ended Period"), ("qa", "DR3 Questions"),
         ("b506", "DR3 Rule 506b"), ("c506", "DR3 Rule 506c"), ("plain", "DR3 Plain"), ("sig", "DR3 Signed"))


async def y_setup(conn):
    section("[Y] Setup — publisher, ten fixture deals created through the API, settings, documents")
    code, _ = await api(U_STAFF, "POST", "/api/v1/deal-room/publishers", {"entity_id": str(E_PUB),
                                                                          "user_ids": [str(U_PUB)]})
    check(code == 201, "staff makes the fixture firm a publisher with its user", f"HTTP {code}")
    S["deals"] = {}
    for key, name in DEALS:
        code, body = await api(U_PUB, "POST", "/api/v1/deal-room/deals", {"name": name, "description": "teaser"})
        S["deals"][key] = body.get("id") if code == 201 else None
    check(all(S["deals"].values()), "the publisher creates the ten deals", dumps(S["deals"]))
    await platform(conn, "UPDATE deals SET deal_status = 'active' WHERE id = ANY($1::uuid[]) AND system_to IS NULL",
                   [UUID(d) for d in S["deals"].values() if d], fetch="execute")
    st = await platform(conn, "SELECT watermark, downloads FROM deal_room_settings WHERE deal_id = $1 AND valid_to IS NULL",
                        UUID(S["deals"]["wm"]), fetch="fetchrow")
    check(dict(st) == {"watermark": "on", "downloads": "allowed"},
          "a NEW deal room starts watermarked with downloads allowed (the spec's default)", dumps(dict(st)))
    t = today()
    for key, body in (("vo", {"downloads": "view_only"}), ("legacy", {"watermark": "off"}),
                      ("exp", {"access_period": "days_after_approval", "access_days": 10, "expiry_notice_days": 7}),
                      ("past", {"access_period": "until_date", "access_until": (t - timedelta(days=1)).isoformat()}),
                      ("b506", {"offering_type": "rule_506b"}), ("c506", {"offering_type": "rule_506c"}),
                      ("wm", {"notify_publisher_on": ["request", "signature", "first_folder_open", "question"]})):
        code, out = await settings_put(S["deals"][key], body)
        check(code == 200 and all(str(out.get(k)) == str(v) if not isinstance(v, list) else out.get(k) == v
                                  for k, v in body.items()), f"settings saved for {key}: {body}", f"{code} {dumps(out)}")
    code, out = await settings_put(S["deals"]["exp"], {"access_period": "days_after_approval", "access_days": 0})
    check(code == 422, "an access period of 0 days is refused (422)", f"{code}")
    pdf3 = make_pdf("om", 3)
    S["pdf3"] = pdf3
    S["docs"] = {}
    for deal, folder, name, data, ctype in (
            ("wm", "02_offering_memorandum", "dr3-om.pdf", pdf3, "application/pdf"),
            ("wm", "02_offering_memorandum", "dr3-model.xlsx", b"PK\x03\x04 not a pdf",
             "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
            ("wm", "02_offering_memorandum", "dr3-chart.png", make_png(), "image/png"),
            ("vo", "02_offering_memorandum", "dr3-vo.pdf", make_pdf("vo", 3), "application/pdf"),
            ("vo", "02_offering_memorandum", "dr3-vo.xlsx", b"PK\x03\x04 sheet",
             "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
            ("legacy", "02_offering_memorandum", "dr3-legacy.pdf", make_pdf("legacy", 1), "application/pdf"),
            ("exp", "02_offering_memorandum", "dr3-exp.pdf", make_pdf("exp", 1), "application/pdf"),
            ("plain", "02_offering_memorandum", "dr3plainom.pdf", make_pdf("plain", 1), "application/pdf"),
            ("b506", "01_overview", "dr3bteaser.pdf", make_pdf("teaser", 1), "application/pdf")):
        code, body = await upload(S["deals"][deal], folder, name, data, ctype)
        check(code == 201, f"{name} filed in {deal}/{folder}", f"HTTP {code} {dumps(body)}")
        S["docs"][name] = body.get("id")


async def y_watermark(conn):
    section("[Y] Watermark — per-page stamp with a traceable id; the original never reaches an investor; "
            "non-watermarkable refused unless shared unwatermarked; managers get the original")
    wm = S["deals"]["wm"]
    since = await now_db(conn)
    code, out = await request(INV1, wm)
    check(code == 200 and out.get("status") == "granted", "the investor requests and is granted (no agreements)",
          f"{code} {dumps(out)}")
    check(len(await notif(conn, "deal_access_granted", INV1, since)) == 1, "investor notified: access approved")
    check(len(await notif(conn, "deal_room_access_requested", U_PUB, since)) == 1,
          "publisher notified: a new request (notify_publisher_on includes request)")
    code, env = await room(INV1, wm)
    docs = docs_in(env)
    blob = json.dumps(env)
    check(code == 200 and docs.get("dr3-om.pdf", {}).get("can_download") is True
          and docs["dr3-om.pdf"].get("watermarked") is True and "r2.mock" not in blob and "r2_key" not in blob,
          "the investor's room lists the PDF as a watermarked download — no URL, no storage key in the envelope",
          dumps(docs.get("dr3-om.pdf")))
    xl = docs.get("dr3-model.xlsx", {})
    check(xl.get("can_download") is False and "cannot be watermarked" in (xl.get("blocked_reason") or ""),
          "the spreadsheet is blocked with the plain reason", dumps(xl))
    check(env.get("notices", {}).get("activity") and "recorded" in env["notices"]["activity"],
          "entering the room shows the activity notice", dumps(env.get("notices")))
    SIGNED_URL_CALLS.clear()
    code, out = await api(INV1, "GET", f"/api/v1/deal-room/deals/{wm}/documents/{S['docs']['dr3-om.pdf']}/download")
    check(code == 200 and "url" not in out and out.get("href", "").endswith("/file") and out.get("watermarked") is True,
          "the download answer names the API's rendition route — never a signed URL", dumps(out))
    before = datetime.now(timezone.utc)
    code, data, hdr = await api_raw(INV1, "GET", f"/api/v1/deal-room/deals/{wm}/documents/{S['docs']['dr3-om.pdf']}/file")
    after = datetime.now(timezone.utc)
    wid = hdr.get("x-watermark-id")
    check(code == 200 and data.startswith(b"%PDF-") and wid is not None, "the watermarked PDF streams from the API",
          f"{code} {hdr.get('content-type')}")
    check(SIGNED_URL_CALLS == [], "no signed URL was minted for the investor (storage.get_signed_url not called)",
          f"{len(SIGNED_URL_CALLS)}")
    check(data != S["pdf3"] and sha(data) != sha(S["pdf3"]), "the investor's bytes are NOT the original's")
    texts = pdf_texts(data) if code == 200 else []
    stamps = {d.strftime("%Y-%m-%d") for d in (before, after)}
    need = ("Ivy Firstinvestor", ENTITY_NAME[INV1], "DR3 Watermark", str(wid), "UTC")
    check(len(texts) == 3 and all(all(n in t for n in need) and any(s in t for s in stamps) for t in texts),
          "EVERY page carries the investor's name, entity, deal, date-time (UTC) and the watermark id",
          dumps([t[-200:] for t in texts]))
    code, w = await api(U_PUB, "GET", f"/api/v1/deal-room/deals/{wm}/watermarks/{wid}")
    check(code == 200 and w.get("user_id") == str(INV1) and w.get("entity_id") == str(ENTITY[INV1])
          and w.get("document_id") == S["docs"]["dr3-om.pdf"] and w.get("kind") == "download",
          "the watermark id resolves to that investor, their entity and the document", dumps(w))
    code_i, _ = await api(INV1, "GET", f"/api/v1/deal-room/deals/{wm}/watermarks/{wid}")
    check(code_i == 403 and code == 200, "the lookup is a manager's tool: investor 403, publisher 200 (identical "
                                         "request)", f"{code_i}/{code}")
    acts = await activity(conn, wm, INV1, "download")
    check(len(acts) == 1 and str(acts[0]["watermark_id"]) == wid, "the rendition wrote a download activity row "
                                                                  "carrying its watermark id", dumps([dict(a) for a in acts]))
    S["wm_activity_inv1"] = acts
    # an image is wrapped into one PDF page and stamped the same way
    code, data2, hdr2 = await api_raw(INV1, "GET", f"/api/v1/deal-room/deals/{wm}/documents/{S['docs']['dr3-chart.png']}/file")
    t2 = pdf_texts(data2) if code == 200 else []
    check(code == 200 and len(t2) == 1 and hdr2.get("x-watermark-id") in t2[0], "a PNG is watermarked too (one stamped "
                                                                                "page)", f"{code}")
    # non-watermarkable: refused with the reason; the rendition route has nothing for it
    xid = S["docs"]["dr3-model.xlsx"]
    code, out = await api(INV1, "GET", f"/api/v1/deal-room/deals/{wm}/documents/{xid}/download")
    c2, _ = await api(INV1, "GET", f"/api/v1/deal-room/deals/{wm}/documents/{xid}/file")
    check(code == 409 and "cannot be watermarked" in json.dumps(out) and c2 == 404,
          "a spreadsheet is refused (409, the reason) and has no rendition (404)", f"{code} {dumps(out)} / {c2}")
    code_i, _ = await api(INV1, "PUT", f"/api/v1/deal-room/deals/{wm}/documents/{xid}/share-unwatermarked", {"share": True})
    flag = await platform(conn, "SELECT share_unwatermarked FROM deal_documents WHERE id = $1", UUID(xid), fetch="fetchval")
    check(code_i == 403 and flag is False, "an investor cannot mark it 'share unwatermarked' (403, unchanged)",
          f"{code_i} {flag}")
    code, out = await api(U_PUB, "PUT", f"/api/v1/deal-room/deals/{wm}/documents/{xid}/share-unwatermarked", {"share": True})
    row = await platform(conn, "SELECT share_unwatermarked, share_unwatermarked_by FROM deal_documents WHERE id = $1",
                         UUID(xid), fetch="fetchrow")
    aud = await platform(conn, "SELECT count(*) FROM audit_log WHERE resource_id = $1 AND action = "
                               "'deal_room.share_unwatermarked'", UUID(xid), fetch="fetchval")
    check(code == 200 and row["share_unwatermarked"] is True and row["share_unwatermarked_by"] == U_PUB and aud == 1,
          "the publisher marks it 'share unwatermarked' — recorded (who) and audited (same request: 403 / 200)",
          f"{code} {dict(row)} audit={aud}")
    SIGNED_URL_CALLS.clear()
    code, out = await api(INV1, "GET", f"/api/v1/deal-room/deals/{wm}/documents/{xid}/download")
    check(code == 200 and out.get("url", "").startswith("https://r2.mock/") and len(SIGNED_URL_CALLS) == 1,
          "once shared unwatermarked, the investor receives that file as it is", dumps(out))
    await api(U_PUB, "PUT", f"/api/v1/deal-room/deals/{wm}/documents/{xid}/share-unwatermarked", {"share": False})
    code, out = await api(U_PUB, "GET", f"/api/v1/deal-room/deals/{wm}/documents/{S['docs']['dr3-om.pdf']}/download")
    check(code == 200 and out.get("url", "").startswith("https://r2.mock/") and out.get("watermarked") is False,
          "a manager always gets the original", dumps(out))
    # legacy (watermark off, downloads allowed): today's behaviour, unchanged
    lg = S["deals"]["legacy"]
    await request(INV1, lg)
    SIGNED_URL_CALLS.clear()
    code, out = await api(INV1, "GET", f"/api/v1/deal-room/deals/{lg}/documents/{S['docs']['dr3-legacy.pdf']}/download")
    check(code == 200 and out.get("url", "").startswith("https://r2.mock/") and len(SIGNED_URL_CALLS) == 1,
          "watermark off + downloads allowed (every existing deal): the investor still gets today's signed URL",
          dumps(out))


async def y_view_only(conn):
    section("[Y] View-only — no investor download; page images render (watermarked); every page view logged")
    vo = S["deals"]["vo"]
    await request(INV1, vo)
    did = S["docs"]["dr3-vo.pdf"]
    code, env = await room(INV1, vo)
    d = docs_in(env)
    check(d.get("dr3-vo.pdf", {}).get("can_download") is False and d["dr3-vo.pdf"].get("can_view") is True
          and "screenshots" in (env.get("notices", {}).get("view_only") or ""),
          "the envelope offers View only, and says view-only cannot stop screenshots", dumps(d.get("dr3-vo.pdf")))
    check(d.get("dr3-vo.xlsx", {}).get("can_view") is False and "cannot be shown" in (d["dr3-vo.xlsx"].get(
        "blocked_reason") or ""), "a spreadsheet under view-only is not viewable (the reason)", dumps(d.get("dr3-vo.xlsx")))
    SIGNED_URL_CALLS.clear()
    c1, _ = await api(INV1, "GET", f"/api/v1/deal-room/deals/{vo}/documents/{did}/download")
    c2, _ = await api(INV1, "GET", f"/api/v1/deal-room/deals/{vo}/documents/{did}/file")
    c3, _ = await api(INV1, "GET", f"/api/v1/deal-room/deals/{vo}/documents/{S['docs']['dr3-vo.xlsx']}/download")
    check((c1, c2, c3) == (404, 404, 404) and SIGNED_URL_CALLS == [],
          "no download route answers the investor (download / rendition / other type: the same 404; no URL minted)",
          f"{c1} {c2} {c3} urls={len(SIGNED_URL_CALLS)}")
    code, info = await api(INV1, "GET", f"/api/v1/deal-room/deals/{vo}/documents/{did}/pages")
    check(code == 200 and info.get("page_count") == 3 and info.get("download") is False,
          "the viewer's page list: 3 pages, no download", dumps(info))
    wids = []
    for n in (1, 2, 3):
        code, png, hdr = await api_raw(INV1, "GET", f"/api/v1/deal-room/deals/{vo}/documents/{did}/pages/{n}")
        ok = code == 200 and png[:8] == b"\x89PNG\r\n\x1a\n" and hdr.get("x-watermark-id")
        wids.append(hdr.get("x-watermark-id"))
        check(bool(ok), f"page {n} renders as a PNG, watermarked (id in the header)", f"{code} {hdr.get('content-type')}")
    c4, _, _ = await api_raw(INV1, "GET", f"/api/v1/deal-room/deals/{vo}/documents/{did}/pages/4")
    check(c4 == 404, "a page past the end is 404", f"{c4}")
    rows = await activity(conn, vo, INV1, "view_page")
    check([r["page"] for r in rows] == [1, 2, 3] and all(str(r["watermark_id"]) in wids for r in rows),
          "every page view is logged with its page and watermark id", dumps([dict(r) for r in rows]))
    reg = await scoped(conn, ORG_A, "SELECT count(*) FROM deal_room_watermarks WHERE document_id = $1 AND kind = 'page' "
                                    "AND user_id = $2", UUID(did), INV1, fetch="fetchval")
    check(reg == 3, "three page renditions in the watermark register", f"{reg}")
    code, _, _ = await api_raw(U_PUB, "GET", f"/api/v1/deal-room/deals/{vo}/documents/{did}/pages/1")
    mgr = await activity(conn, vo, U_PUB)
    check(code == 200 and mgr == [], "a manager views pages too, unlogged (the log is the investors')", f"{code} {len(mgr)}")


async def y_expiry(conn):
    section("[Y] Expiry — end date at approval; the end date itself open, the next day closed; 7-day notice; "
            "extend (audited); advisors close with the investor; the live tick skips is_test orgs")
    ex = S["deals"]["exp"]
    t = today()
    code, out = await request(INV2, ex)
    acc = await access_row(conn, ex, INV2)
    ends = t + timedelta(days=10)
    check(code == 200 and acc is not None and acc["status"] == "granted" and acc["access_ends_on"] == ends,
          "approval stamps access_ends_on = approval day + 10 (days_after_approval)", f"{dumps(out)} {dict(acc or {})}")
    f02 = await folder_id(conn, ex, "02_offering_memorandum")
    # an advisor on this deal — closes with the investor
    code, out = await api(INV2, "POST", f"/api/v1/deal-room/deals/{ex}/advisors",
                          {"name": "Alex Advisor", "firm": "Counsel LLP", "role": "lawyer", "email": email_of(U_ADV)})
    _, ack = await api(U_ADV, "GET", f"/api/v1/deal-room/deals/{ex}/advisor-acknowledgement")
    c2, _ = await api(U_ADV, "POST", f"/api/v1/deal-room/deals/{ex}/advisor-acknowledgement",
                      {"typed_name": "Alex Advisor", "consent": True, "text_sha256": ack.get("text_sha256")})
    check(code == 201 and c2 == 200 and await folder_open(conn, f02, U_ADV),
          "an acknowledged advisor reads the investor's folder while the investor's access runs", f"{code} {c2}")
    since = await now_db(conn)
    r8 = await sweep(ends - timedelta(days=8))
    n8 = len(await notif(conn, "deal_access_expiring", INV2, since))
    r7 = await sweep(ends - timedelta(days=7))
    n7 = await notif(conn, "deal_access_expiring", INV2, since)
    await sweep(ends - timedelta(days=7))
    n7b = len(await notif(conn, "deal_access_expiring", INV2, since))
    check(n8 == 0 and len(n7) == 1 and n7b == 1 and n7[0]["email_pending"] is True,
          "the expiring notice goes 7 days before (not 8), once (a second sweep sends none), email recorded pending",
          f"{n8}/{len(n7)}/{n7b} {dumps(r7)}")
    await sweep(ends)
    acc = await access_row(conn, ex, INV2)
    check(acc["status"] == "granted" and await folder_open(conn, f02, INV2),
          "ON the end date access is still open", f"{dict(acc)}")
    res = await sweep(ends + timedelta(days=1))
    acc = await access_row(conn, ex, INV2)
    check(acc["status"] == "expired" and not await folder_open(conn, f02, INV2),
          "the day AFTER the end date: expired, folders closed", f"{dict(acc)} {dumps(res)}")
    check(not await folder_open(conn, f02, U_ADV), "the advisor's folders closed with the investor's, at once")
    check(len(await notif(conn, "deal_access_expired", INV2, since)) == 1
          and len(await notif(conn, "deal_access_expired_publisher", U_PUB, since)) == 1,
          "investor and publisher notified on expiry")
    code, env = await room(INV2, ex)
    check(code == 200 and env["my_access"]["status"] == "expired" and not any(f.get("documents") for f in env["folders"]
                                                                               if f["access_level"] != "public"),
          "the expired investor sees the deal and why, but no folder above public", dumps(env.get("my_access")))
    new_end = t + timedelta(days=30)
    c_i, _ = await decide(INV2, acc["id"], "extend", ends_on=new_end.isoformat())
    c_p, out = await decide(U_PUB, acc["id"], "extend", ends_on=new_end.isoformat())
    acc2 = await access_row(conn, ex, INV2)
    aud = await platform(conn, "SELECT payload FROM audit_log WHERE action = 'deal_access.extend' AND resource_id = $1",
                         acc2["id"], fetch="fetchval")
    check(c_i == 403 and c_p == 200 and acc2["status"] == "granted" and acc2["access_ends_on"] == new_end
          and acc2["level_granted"] == "after_agreements" and aud is not None,
          "extend: investor 403, publisher 200 (identical request) → granted again at its level, new end date, audited",
          f"{c_i}/{c_p} {dumps(out)} {dict(acc2)}")
    c_past, _ = await decide(U_PUB, acc2["id"], "extend", ends_on=(t - timedelta(days=1)).isoformat())
    check(c_past == 422, "an end date in the past is refused", f"{c_past}")
    c_none, _ = await api(U_PUB, "POST", f"/api/v1/deal-room/deals/{ex}/advisors/{U_ADV}/remove", {"reason": ""})
    c_rm, out = await api(U_PUB, "POST", f"/api/v1/deal-room/deals/{ex}/advisors/{U_ADV}/remove",
                          {"reason": "Engagement ended"})
    rm = await scoped(conn, ORG_A, "SELECT status, revoked_reason FROM deal_room_advisors WHERE deal_id = $1 AND "
                                   "advisor_user_id = $2 AND valid_to IS NULL", UUID(ex), U_ADV, fetch="fetchrow")
    check(c_none == 422 and c_rm == 200 and rm["status"] == "revoked" and rm["revoked_reason"] == "Engagement ended",
          "the publisher removes an advisor — reason required and recorded", f"{c_none}/{c_rm} {dict(rm)}")
    # the live tick never sweeps a test org; a verify tick of its own org does
    from services import deal_room_control as C
    from services.database import get_pool

    pa = S["deals"]["past"]
    await request(INV1, pa)
    acc = await access_row(conn, pa, INV1)
    check(acc["access_ends_on"] == t - timedelta(days=1) and acc["status"] == "granted",
          "an until_date period in the past stamps that date at approval", f"{dict(acc)}")
    live = await C.access_tick(await get_pool(), today=t)
    acc = await access_row(conn, pa, INV1)
    check(str(ORG_A) not in live and acc["status"] == "granted",
          "the live scheduler step (access_tick) skips the is_test org — the due row is untouched", f"{list(live)[:5]}")
    skipped = await sweep(t, include_test_orgs=False)
    check(skipped.get("skipped") == "test org", "the per-org sweep refuses a test org unless asked", dumps(skipped))
    await sweep(t)
    acc = await access_row(conn, pa, INV1)
    check(acc["status"] == "expired", "a verify tick of its own org (include_test_orgs=True) expires it", f"{dict(acc)}")


async def y_pause(conn):
    section("[Y] Pause — a lapsed re-verification / KYC pauses at once (folder function and Document Search agree); "
            "restored automatically when the record is fixed")
    pl, sg = S["deals"]["plain"], S["deals"]["sig"]
    t = today()
    await request(INV3, pl)
    f02 = await folder_id(conn, pl, "02_offering_memorandum")
    code, s0 = await search(INV3, "dr3plainom")
    hit0 = any(r.get("parent_id") == pl for r in s0.get("results", []))
    check((await access_row(conn, pl, INV3))["status"] == "granted" and await folder_open(conn, f02, INV3) and hit0,
          "before: granted, the folder is open, Document Search finds the document", f"{code} {dumps(s0)}")
    since = await now_db(conn)
    code, out = await compliance(ENTITY[INV3], {"next_reverification_due": (t - timedelta(days=1)).isoformat()})
    acc = await access_row(conn, pl, INV3)
    code_s, s1 = await search(INV3, "dr3plainom")
    check(code == 200 and acc["status"] == "paused" and "re-verification" in (acc["paused_reason"] or "")
          and acc["prior_level"] == "after_agreements",
          "a re-verification date in the past pauses access AT ONCE (same request), keeping the level to restore",
          f"{code} {dumps(out)} {dict(acc)}")
    check(not await folder_open(conn, f02, INV3) and not any(r.get("parent_id") == pl for r in s1.get("results", [])),
          "the folder function and Document Search agree: closed", f"{code_s} {dumps(s1)}")
    check(len(await notif(conn, "deal_access_paused", INV3, since)) == 1
          and len(await notif(conn, "deal_access_paused_staff", U_STAFF, since)) == 1,
          "the investor and staff are told")
    code, env = await room(INV3, pl)
    check(bool(env["my_access"]["status"] == "paused" and env["my_access"]["paused_reason"]),
          "the room shows the paused reason", dumps(env.get("my_access")))
    code, out = await compliance(ENTITY[INV3], {"next_reverification_due": (t + timedelta(days=365)).isoformat()})
    acc = await access_row(conn, pl, INV3)
    aud = await platform(conn, "SELECT count(*) FROM audit_log WHERE action = 'deal_access.restore' AND resource_id = $1",
                         acc["id"], fetch="fetchval")
    code_s, s2 = await search(INV3, "dr3plainom")
    check(acc["status"] == "granted" and acc["level_granted"] == "after_agreements" and aud == 1
          and await folder_open(conn, f02, INV3) and any(r.get("parent_id") == pl for r in s2.get("results", [])),
          "fixing the record restores access automatically (logged), the folder and search reopen", f"{dict(acc)} {aud}")
    check(len(await notif(conn, "deal_access_restored", INV3, since)) == 1, "the investor is told it reopened")
    # the date passing with no write: the sweep pauses, then restores once the date is fixed
    await compliance(ENTITY[INV3], {"next_reverification_due": (t + timedelta(days=3)).isoformat()})
    check((await access_row(conn, pl, INV3))["status"] == "granted", "a future re-verification date changes nothing")
    await sweep(t + timedelta(days=4))
    p = await access_row(conn, pl, INV3)
    await sweep(t)
    r = await access_row(conn, pl, INV3)
    check(p["status"] == "paused" and r["status"] == "granted", "the sweep pauses once the date passes, and restores "
                                                               "when it no longer has", f"{p['status']} → {r['status']}")
    # KYC no longer passing; a signed investor keeps seeing their signatures
    tpl = await platform(conn, "SELECT id FROM agreement_templates WHERE key = 'platform_nda' AND version = 1 "
                               "AND org_id IS NULL", fetch="fetchval")
    code, a = await api(U_PUB, "POST", f"/api/v1/deal-room/deals/{sg}/agreements",
                        {"template_id": str(tpl), "field_values": {"publisher_entity_type": "limited liability company"}})
    c_pub, _ = await api(U_PUB, "POST", f"/api/v1/deal-room/deals/{sg}/agreements/{a.get('id')}/publish")
    since_sig = await now_db(conn)
    await request(INV3, sg)
    _, doc = await api(INV3, "GET", f"/api/v1/deal-room/deals/{sg}/agreements/{a.get('id')}/render")
    c_sign, sig = await api(INV3, "POST", f"/api/v1/deal-room/deals/{sg}/agreements/{a.get('id')}/sign",
                            {"typed_name": USERS[INV3][4], "consent": True,
                             "rendered_text_sha256": doc.get("rendered_text_sha256")})
    check(c_pub == 200 and c_sign == 201 and sig.get("status") == "granted", "the investor signs the NDA and is granted",
          f"{c_pub} {c_sign} {dumps(sig)}")
    check(len(await notif(conn, "deal_room_agreement_signed", U_PUB, since_sig)) == 1, "publisher notified: signature")
    S["sig_agreement"] = a.get("id")
    await compliance(ENTITY[INV3], {"kyc_status": "in_progress"})
    p1, p2 = await access_row(conn, pl, INV3), await access_row(conn, sg, INV3)
    code, env = await room(INV3, sg)
    signed = [x for x in env.get("agreements", []) if x.get("signature_id")]
    closed = all(not f["open"] for f in env.get("folders", []) if f["access_level"] != "public")
    check(p1["status"] == p2["status"] == "paused" and "KYC" in (p2["paused_reason"] or ""),
          "KYC no longer approved pauses every granted deal of that entity", f"{dict(p1)} {dict(p2)}")
    check(len(signed) == 1 and closed, "a paused investor still sees their signature, and no folder above public",
          dumps(env.get("agreements")))
    await compliance(ENTITY[INV3], {"kyc_status": "approved"})
    check((await access_row(conn, sg, INV3))["status"] == "granted", "KYC approved again restores it")


async def y_qa(conn):
    section("[Y] Q&A — answers reach exactly the investors whose level reaches the folder; asker hidden; "
            "withdraw / merge; notifications")
    qa = S["deals"]["qa"]
    for uid in (INV1, INV2):
        await request(uid, qa)
    acc2 = await access_row(conn, qa, INV2)
    c, out = await decide(U_PUB, acc2["id"], "approve", level="after_approval")
    check(c == 200, "the publisher raises investor 2 to after_approval", f"{c} {dumps(out)}")
    f02 = await folder_id(conn, qa, "02_offering_memorandum")
    f05 = await folder_id(conn, qa, "05_legal_structure")
    since = await now_db(conn)
    q = {}
    c1, o1 = await api(INV2, "POST", f"/api/v1/deal-room/deals/{qa}/questions",
                       {"body": "What does the operating agreement say on transfers?", "folder_id": str(f05)})
    c2, o2 = await api(INV1, "POST", f"/api/v1/deal-room/deals/{qa}/questions", {"body": "What is the hold period?"})
    c3, o3 = await api(INV1, "POST", f"/api/v1/deal-room/deals/{qa}/questions",
                       {"body": "Is the OM's fee table final?", "folder_id": str(f02)})
    c4, _ = await api(INV1, "POST", f"/api/v1/deal-room/deals/{qa}/questions",
                      {"body": "A question about legal?", "folder_id": str(f05)})
    c5, _ = await api(INV4, "POST", f"/api/v1/deal-room/deals/{qa}/questions", {"body": "Can I ask without access?"})
    q.update(q05=o1.get("id"), qg=o2.get("id"), q02=o3.get("id"))
    check((c1, c2, c3) == (201, 201, 201) and c4 == 404 and c5 == 409,
          "investors ask (general or a folder they can open); a closed folder is 404; no access is 409",
          f"{c1} {c2} {c3} {c4} {c5}")
    check(len(await notif(conn, "deal_room_question_asked", U_PUB, since)) == 3, "publisher notified of each question")
    c_i, _ = await api(INV1, "POST", f"/api/v1/deal-room/deals/{qa}/questions/{q['qg']}/answer", {"body": "Five years."})
    codes = []
    for key, text in (("q05", "Transfers need consent."), ("qg", "Five years."), ("q02", "Yes, final.")):
        cc, _ = await api(U_PUB, "POST", f"/api/v1/deal-room/deals/{qa}/questions/{q[key]}/answer", {"body": text})
        codes.append(cc)
    check(c_i == 403 and codes == [200, 200, 200], "answering: investor 403, publisher 200 (identical request)",
          f"{c_i} {codes}")
    _, l1 = await api(INV1, "GET", f"/api/v1/deal-room/deals/{qa}/questions")
    _, l2 = await api(INV2, "GET", f"/api/v1/deal-room/deals/{qa}/questions")
    _, l4 = await api(INV4, "GET", f"/api/v1/deal-room/deals/{qa}/questions")
    ids1 = {r["id"] for r in l1.get("rows", [])}
    ids2 = {r["id"] for r in l2.get("rows", [])}
    check(ids1 == {q["qg"], q["q02"]} and ids2 == {q["q05"], q["qg"], q["q02"]} and not l4.get("rows"),
          "visibility: after_agreements sees general + 02; after_approval also sees 05; no access sees none",
          f"{len(ids1)} {len(ids2)} {len(l4.get('rows', []))}")
    blob1 = json.dumps(l1)
    check(all("asker_name" not in r and "asked_by" not in r for r in l1["rows"]) and str(INV2) not in blob1
          and USERS[INV2][4] not in blob1, "an investor never sees who asked (no asker field, no id, no name)")
    by = {r["id"]: r for r in l1["rows"]}
    check(by[q["qg"]]["mine"] is True and by[q["q02"]]["answer"] == "Yes, final.", "the asker sees their own as "
                                                                                  "'mine'; answers are present")
    r05 = await notif(conn, "deal_room_question_answered", INV2, since)
    r1 = await notif(conn, "deal_room_question_answered", INV1, since)
    check(any(q["q05"] in str(n["payload"]) for n in r05) and not any(q["q05"] in str(n["payload"]) for n in r1)
          and any(q["qg"] in str(n["payload"]) for n in r1),
          "answer notifications reach exactly the level-scoped readers (05 → investor 2 only; general → both)",
          f"{len(r05)} {len(r1)}")
    code, env = await room(INV1, qa)
    qa_folder = next((f for f in env.get("folders", []) if f["key"] == "07_qa"), {})
    check({x["id"] for x in qa_folder.get("answered_questions", [])} == {q["qg"], q["q02"]},
          "the 07 Q&A folder lists the answered questions the reader may see", dumps(qa_folder)[:300])
    # merge a duplicate; withdraw one (reason recorded); who sees what
    _, d = await api(INV2, "POST", f"/api/v1/deal-room/deals/{qa}/questions", {"body": "How long is the hold?"})
    _, w = await api(INV1, "POST", f"/api/v1/deal-room/deals/{qa}/questions", {"body": "Off-topic question here"})
    c_nr, _ = await api(U_PUB, "POST", f"/api/v1/deal-room/deals/{qa}/questions/{w.get('id')}/withdraw", {"reason": ""})
    c_m, mo = await api(U_PUB, "POST", f"/api/v1/deal-room/deals/{qa}/questions/{d.get('id')}/merge",
                        {"into_id": q["qg"], "reason": "Duplicate of the hold-period question"})
    c_w, wo = await api(U_PUB, "POST", f"/api/v1/deal-room/deals/{qa}/questions/{w.get('id')}/withdraw",
                        {"reason": "Not about this deal"})
    _, l1 = await api(INV1, "GET", f"/api/v1/deal-room/deals/{qa}/questions")
    _, l2 = await api(INV2, "GET", f"/api/v1/deal-room/deals/{qa}/questions")
    mine_w = next((r for r in l1["rows"] if r["id"] == w.get("id")), {})
    mine_d = next((r for r in l2["rows"] if r["id"] == d.get("id")), {})
    check(c_nr == 422 and c_m == 200 and c_w == 200 and mo.get("merged_into") == q["qg"],
          "withdraw needs a reason; merge and withdraw succeed", f"{c_nr} {c_m} {c_w}")
    check(mine_w.get("status") == "withdrawn" and mine_w.get("status_reason") == "Not about this deal"
          and mine_d.get("status") == "merged" and mine_d.get("merged_into") == q["qg"],
          "each asker sees their own question withdrawn / merged, with the reason", f"{mine_w} {mine_d}")
    check(w.get("id") not in {r["id"] for r in l2["rows"]} and d.get("id") not in {r["id"] for r in l1["rows"]},
          "another investor sees neither the withdrawn nor the merged question")
    hist = await scoped(conn, ORG_A, "SELECT count(*) FROM deal_room_questions WHERE history_of = $1",
                        UUID(w.get("id")), fetch="fetchval")
    check(hist >= 1, "the question's earlier state is kept (system-axis history row)", f"{hist}")
    acts = await activity(conn, qa, INV1)
    kinds = {a["action"] for a in acts}
    check({"question", "answer_read", "room_open"} <= kinds, "asking and reading answers are logged", f"{kinds}")
    S["q"] = q


async def y_advisors(conn):
    section("[Y] Advisors — acknowledgement first; exactly the investor's open folders; cannot sign / ask / invite; "
            "on the grid; revoked with the investor")
    wm = S["deals"]["wm"]
    code, out = await api(INV1, "POST", f"/api/v1/deal-room/deals/{wm}/advisors",
                          {"name": "Alex Advisor", "firm": "Counsel LLP", "role": "lawyer", "email": email_of(U_ADV)})
    check(code == 201 and out.get("how") == "linked" and out.get("status") == "invited",
          "an existing user of the firm is linked (status invited)", f"{code} {dumps(out)}")
    INVITE_EMAILS.clear()
    code, out = await api(INV1, "POST", f"/api/v1/deal-room/deals/{wm}/advisors",
                          {"name": "Nadia Newadvisor", "role": "accountant", "email": NEW_ADVISOR_EMAIL})
    new_row = await platform(conn, "SELECT id, org_id, invite_status, auth0_sub FROM users WHERE email = $1",
                             NEW_ADVISOR_EMAIL, fetch="fetchrow")
    check(code == 201 and out.get("how") == "invited" and new_row is not None and new_row["org_id"] == ORG_A
          and new_row["invite_status"] == "pending" and new_row["auth0_sub"] is None and INVITE_EMAILS == [NEW_ADVISOR_EMAIL],
          "an unknown email is invited through the usermgmt1 path into the deal's org (email stubbed)",
          f"{code} {dumps(out)} {dict(new_row or {})}")
    code, out = await api(INV1, "POST", f"/api/v1/deal-room/deals/{wm}/advisors",
                          {"name": "Bea Otherfirm", "role": "other", "email": email_of(U_B_INV)})
    check(code == 409, "an address that belongs to another firm is refused (409), never linked across orgs",
          f"{code} {dumps(out)}")
    # before the acknowledgement: nothing
    code, env = await room(U_ADV, wm)
    did = S["docs"]["dr3-om.pdf"]
    c_dl, _ = await api(U_ADV, "GET", f"/api/v1/deal-room/deals/{wm}/documents/{did}/download")
    c_pg, _ = await api(U_ADV, "GET", f"/api/v1/deal-room/deals/{wm}/documents/{did}/pages")
    c_q, _ = await api(U_ADV, "GET", f"/api/v1/deal-room/deals/{wm}/questions")
    check(code == 200 and env.get("advisor", {}).get("needs_ack") is True and not any(f["open"] for f in env["folders"])
          and not docs_in(env) and (c_dl, c_pg, c_q) == (404, 404, 404),
          "before acknowledging: every folder closed, no file names, downloads / pages / Q&A all 404",
          f"{code} {c_dl} {c_pg} {c_q}")
    _, ack = await api(U_ADV, "GET", f"/api/v1/deal-room/deals/{wm}/advisor-acknowledgement")
    bad_name, _ = await api(U_ADV, "POST", f"/api/v1/deal-room/deals/{wm}/advisor-acknowledgement",
                            {"typed_name": "Someone Else", "consent": True, "text_sha256": ack.get("text_sha256")})
    bad_sha, _ = await api(U_ADV, "POST", f"/api/v1/deal-room/deals/{wm}/advisor-acknowledgement",
                           {"typed_name": "Alex Advisor", "consent": True, "text_sha256": "0" * 64})
    no_consent, _ = await api(U_ADV, "POST", f"/api/v1/deal-room/deals/{wm}/advisor-acknowledgement",
                              {"typed_name": "Alex Advisor", "consent": False, "text_sha256": ack.get("text_sha256")})
    good, out = await api(U_ADV, "POST", f"/api/v1/deal-room/deals/{wm}/advisor-acknowledgement",
                          {"typed_name": "Alex Advisor", "consent": True, "text_sha256": ack.get("text_sha256")})
    check("bound by the same confidentiality" in (ack.get("text") or "") and "responsible" in ack.get("text", "")
          and (bad_name, bad_sha, no_consent, good) == (422, 409, 422, 200),
          "the acknowledgement (same confidentiality; the investor responsible) needs the advisor's name, consent "
          "and the text they read", f"{bad_name} {bad_sha} {no_consent} {good}")
    inv_set, adv_set = await open_set(conn, wm, INV1), await open_set(conn, wm, U_ADV)
    all_folders = {r["id"] for r in await platform(conn, "SELECT id FROM deal_folders WHERE deal_id = $1 AND "
                                                         "system_to IS NULL", UUID(wm))}
    check(adv_set == inv_set and inv_set != all_folders and len(inv_set) > 1,
          "after acknowledging, the advisor's open folders are EXACTLY the investor's (not more)",
          f"{len(adv_set)} vs {len(inv_set)} of {len(all_folders)}")
    code, out = await api(U_ADV, "GET", f"/api/v1/deal-room/deals/{wm}/documents/{did}/download")
    code2, data, hdr = await api_raw(U_ADV, "GET", f"/api/v1/deal-room/deals/{wm}/documents/{did}/file")
    _, w = await api(U_PUB, "GET", f"/api/v1/deal-room/deals/{wm}/watermarks/{hdr.get('x-watermark-id')}")
    check(code == 200 and code2 == 200 and w.get("user_id") == str(U_ADV) and w.get("on_behalf_of_user_id") == str(INV1)
          and "advisor to Ivy Firstinvestor" in " ".join(pdf_texts(data)),
          "the advisor's copy is stamped as theirs (advisor to the investor) and traces to them", dumps(w))
    sg_aid = S.get("sig_agreement") or str(_id(999))
    refused = []
    for method, path, body in (
            ("POST", f"/api/v1/deal-room/deals/{wm}/agreements/{sg_aid}/sign",
             {"typed_name": "Alex Advisor", "consent": True, "rendered_text_sha256": "0" * 64}),
            ("POST", f"/api/v1/deal-room/deals/{wm}/access/request", {"entity_id": str(ENTITY[INV1])}),
            ("POST", f"/api/v1/deal-room/deals/{wm}/questions", {"body": "May I ask for my client?"}),
            ("POST", f"/api/v1/deal-room/deals/{wm}/advisors", {"name": "Second Hop", "role": "other",
                                                                "email": f"{TAG}-hop@test.local"})):
        c, _ = await api(U_ADV, method, path, body)
        refused.append(c)
    hop = await platform(conn, "SELECT count(*) FROM users WHERE email = $1", f"{TAG}-hop@test.local", fetch="fetchval")
    check(refused == [403, 403, 403, 403] and hop == 0,
          "the advisor cannot sign, request access, ask a question or invite anyone (403 each; nothing created)",
          f"{refused} hop={hop}")
    row = await grid_row(wm, INV1)
    names = [a["name"] for a in (row or {}).get("advisors", [])]
    check(row is not None and "Alex Advisor" in names and "Nadia Newadvisor" in names,
          "advisors appear on the grid under their investor", dumps(row and row.get("advisors")))
    _, envi = await room(INV2, wm)
    check("Alex Advisor" not in json.dumps(envi), "another investor never sees someone else's advisors")
    S["adv_active"] = True


async def y_revoke_notice(conn):
    section("[Y] Revoke with the return-or-destroy notice — recorded, sent, acknowledged; advisors end at once; "
            "activity retained after revoke and after the deal closes")
    wm = S["deals"]["wm"]
    acc = await access_row(conn, wm, INV1)
    n_before = len(await activity(conn, wm))
    since = await now_db(conn)
    c_bad, _ = await decide(U_PUB, acc["id"], "approve", level="after_agreements", send_return_notice=True)
    check(c_bad == 422, "a return-or-destroy notice goes only with a revoke (422 otherwise)", f"{c_bad}")
    c, out = await decide(U_PUB, acc["id"], "revoke", reason="Allocation closed", send_return_notice=True)
    res = (out.get("results") or [{}])[0]
    n = await scoped(conn, ORG_A, "SELECT id, notice_text, notice_sha256, acknowledged_at FROM deal_room_return_notices "
                                  "WHERE deal_id = $1 AND user_id = $2 AND valid_to IS NULL", UUID(wm), INV1,
                     fetch="fetchrow")
    check(c == 200 and res.get("status") == "revoked" and res.get("return_notice_id") and n is not None
          and "return or destroy" in n["notice_text"] and n["acknowledged_at"] is None,
          "revoke records the notice (its text and hash) against the revoked row", f"{c} {dumps(out)}")
    sent = await notif(conn, "deal_room_return_notice", INV1, since)
    check(len(sent) == 1 and sent[0]["email_pending"] is True, "the notice is sent in-app (email recorded pending)")
    adv = await scoped(conn, ORG_A, "SELECT status FROM deal_room_advisors WHERE deal_id = $1 AND access_user_id = $2 "
                                    "AND valid_to IS NULL", UUID(wm), INV1)
    c_dl, _ = await api(U_ADV, "GET", f"/api/v1/deal-room/deals/{wm}/documents/{S['docs']['dr3-om.pdf']}/download")
    non_public = {r["id"] for r in await platform(conn, "SELECT id FROM deal_folders WHERE deal_id = $1 AND "
                                                        "system_to IS NULL AND access_level <> 'public'", UUID(wm))}
    check(adv and all(r["status"] == "revoked" for r in adv) and not (await open_set(conn, wm, U_ADV) & non_public)
          and c_dl == 404, "revoking the investor ends every advisor at once (rows revoked, folders closed, 404)",
          f"{[r['status'] for r in adv]} {c_dl}")
    row = await grid_row(wm, INV1)
    check(row and row.get("return_notice") and row["return_notice"]["acknowledged_at"] is None,
          "the grid shows the notice sent, not yet acknowledged", dumps(row and row.get("return_notice")))
    c_wrong, _ = await api(INV1, "POST", f"/api/v1/deal-room/deals/{wm}/return-notice/acknowledge",
                           {"typed_name": "Not Me"})
    c_ok, ok = await api(INV1, "POST", f"/api/v1/deal-room/deals/{wm}/return-notice/acknowledge",
                         {"typed_name": USERS[INV1][4]})
    c_again, _ = await api(INV1, "POST", f"/api/v1/deal-room/deals/{wm}/return-notice/acknowledge",
                           {"typed_name": USERS[INV1][4]})
    row = await grid_row(wm, INV1)
    hist = await scoped(conn, ORG_A, "SELECT count(*) FROM deal_room_return_notices WHERE access_id = (SELECT access_id "
                                     "FROM deal_room_return_notices WHERE id = $1)", n["id"], fetch="fetchval")
    check(c_wrong == 422 and c_ok == 200 and c_again == 409 and row["return_notice"]["acknowledged_at"] and hist == 2,
          "the investor acknowledges (typed name; once); the grid shows it; the earlier state is kept",
          f"{c_wrong} {c_ok} {c_again} hist={hist}")
    _, mine = await api(INV1, "GET", "/api/v1/deal-room/my-access")
    r = next((x for x in mine.get("rows", []) if x["deal_id"] == wm), {})
    check((r.get("return_notice") or {}).get("acknowledged_at") is not None, "My deal access shows the acknowledgement")
    n_after = len(await activity(conn, wm))
    check(n_after == n_before and n_before > 0, "every activity row is retained after the revoke",
          f"{n_before} → {n_after}")
    # the deal closes: nothing is deleted, the manager still reads it all
    await platform(conn, "UPDATE deals SET deal_status = 'closed' WHERE id = $1 AND system_to IS NULL", UUID(wm),
                   fetch="execute")
    code, view = await api(U_PUB, "GET", f"/api/v1/deal-room/deals/{wm}/activity?view=events")
    check(code == 200 and len(view.get("rows", [])) == n_before and len(await activity(conn, wm)) == n_before,
          "after the deal closes, every activity row is still there and readable by the manager",
          f"{code} {len(view.get('rows', []))}")
    await platform(conn, "UPDATE deals SET deal_status = 'active' WHERE id = $1 AND system_to IS NULL", UUID(wm),
                   fetch="execute")


async def y_activity(conn):
    section("[Y] Activity — complete, private to managers, append-only, CSV matches")
    wm = S["deals"]["wm"]
    since = await now_db(conn)
    f02 = await folder_id(conn, S["deals"]["qa"], "02_offering_memorandum")
    c1, _ = await api(INV2, "POST", f"/api/v1/deal-room/deals/{S['deals']['qa']}/folders/{f02}/open")
    c2, _ = await api(INV2, "POST", f"/api/v1/deal-room/deals/{S['deals']['qa']}/folders/{f02}/open")
    f05c = await folder_id(conn, S["deals"]["plain"], "05_legal_structure")
    c3, _ = await api(INV3, "POST", f"/api/v1/deal-room/deals/{S['deals']['plain']}/folders/{f05c}/open")
    opens = await activity(conn, S["deals"]["qa"], INV2, "folder_open")
    check(c1 == c2 == 200 and c3 == 404 and len(opens) == 2, "opening a folder is logged each time; a closed folder is "
                                                             "404 and logs nothing", f"{c1} {c2} {c3} {len(opens)}")
    # first-open notification (only when the deal's setting asks: wm has it, qa does not)
    await request(INV2, wm)
    fw = await folder_id(conn, wm, "02_offering_memorandum")
    s2 = await now_db(conn)
    await api(INV2, "POST", f"/api/v1/deal-room/deals/{wm}/folders/{fw}/open")
    await api(INV2, "POST", f"/api/v1/deal-room/deals/{wm}/folders/{fw}/open")
    ff = await notif(conn, "deal_room_first_folder_open", U_PUB, s2)
    qa_ff = await notif(conn, "deal_room_first_folder_open", U_PUB, since)
    check(len(ff) == 1 and len(qa_ff) == 1, "the publisher hears of the FIRST open only, and only where the setting is "
                                            "on", f"{len(ff)} {len(qa_ff)}")
    # a new document reaches exactly the readers of its folder
    s3 = await now_db(conn)
    code, _ = await upload(wm, "02_offering_memorandum", "dr3-update.pdf", make_pdf("update", 1), "application/pdf")
    to2 = await notif(conn, "deal_room_new_document", INV2, s3)
    to4 = await notif(conn, "deal_room_new_document", INV4, s3)
    to1 = await notif(conn, "deal_room_new_document", INV1, s3)
    check(code == 201 and len(to2) == 1 and to2[0]["email_pending"] and not to4 and not to1,
          "a new document notifies the investors its folder is open to — not one without access, not a revoked one",
          f"{code} {len(to2)} {len(to4)} {len(to1)}")
    # the manager's views, and the CSV
    c_i, _ = await api(INV2, "GET", f"/api/v1/deal-room/deals/{wm}/activity")
    c_p, inv_view = await api(U_PUB, "GET", f"/api/v1/deal-room/deals/{wm}/activity?view=investor")
    check(c_i == 403 and c_p == 200, "activity: investor 403, publisher 200 (identical request)", f"{c_i}/{c_p}")
    direct = await scoped(conn, ORG_A, """
        SELECT user_id::text AS u, count(*) FILTER (WHERE action = 'download') AS dl,
               count(*) FILTER (WHERE action = 'view_page') AS pages, count(DISTINCT document_id)
                 FILTER (WHERE action = 'download') AS docs_dl
        FROM deal_room_activity WHERE deal_id = $1 GROUP BY user_id""", UUID(wm))
    dmap = {r["u"]: (int(r["dl"]), int(r["pages"]), int(r["docs_dl"])) for r in direct}
    vmap = {r["user_id"]: (int(r["downloads"]), int(r["pages_viewed"]), int(r["documents_downloaded"]))
            for r in inv_view.get("rows", [])}
    check(len(vmap) >= 2 and vmap == dmap, "per-investor figures equal the same aggregate run directly in SQL",
          f"{vmap} vs {dmap}")
    inv1 = next((r for r in inv_view["rows"] if r["user_id"] == str(INV1)), {})
    check(inv1.get("first_open") and inv1.get("last_open") and inv1["first_open"] <= inv1["last_open"]
          and int(inv1.get("documents_downloaded", 0)) >= 2, "first / last open and documents downloaded per investor",
          dumps(inv1))
    _, doc_view = await api(U_PUB, "GET", f"/api/v1/deal-room/deals/{wm}/activity?view=document")
    om = next((r for r in doc_view.get("rows", []) if r["document_id"] == S["docs"]["dr3-om.pdf"]), {})
    check(int(om.get("downloads", 0)) >= 2 and int(om.get("readers", 0)) >= 2, "per-document view: readers and "
                                                                                 "downloads", dumps(om))
    for view in ("investor", "document", "events"):
        res = await _request(U_PUB, "GET", f"/api/v1/deal-room/deals/{wm}/activity?view={view}&format=csv")
        _, js = await api(U_PUB, "GET", f"/api/v1/deal-room/deals/{wm}/activity?view={view}")
        rows = list(csv.DictReader(io.StringIO(res.text))) if res is not None else []
        norm = [{k: ("" if v is None else str(v)) for k, v in r.items()} for r in js.get("rows", [])]
        check(res is not None and res.status_code == 200 and res.headers.get("content-type", "").startswith("text/csv")
              and len(rows) == len(norm) > 0 and [r[c] for r in rows for c in ("user_id", "action", "document_id")
                                                  if c in r] == [r[c] for r in norm for c in ("user_id", "action",
                                                                                              "document_id") if c in r],
              f"CSV export ({view}) has the same rows as the JSON view", f"{len(rows)} vs {len(norm)}")
    _, env = await room(INV2, wm)
    blob = json.dumps(env)
    check(USERS[INV1][4] not in blob and str(INV1) not in blob and "activity" not in (env.get("vocabularies") or {}).get(
        "editable", []), "an investor's room carries nobody else's name, id or activity")
    # append-only, both layers
    some = (await activity(conn, wm))[0]["id"]
    upd = await scoped(conn, ORG_A, "UPDATE deal_room_activity SET page = 99 WHERE id = $1", some, fetch="execute")
    dele = await scoped(conn, ORG_A, "DELETE FROM deal_room_activity WHERE id = $1", some, fetch="execute")
    check(upd.endswith(" 0") and dele.endswith(" 0"), "app_service: UPDATE / DELETE of the log match zero rows (no "
                                                      "policy)", f"{upd} {dele}")
    op = await operator_conn()
    try:
        raised = None
        try:
            async with op.transaction():
                await op.execute("DELETE FROM deal_room_activity WHERE id = $1", some)
        except Exception as exc:  # noqa: BLE001
            raised = str(exc)
    finally:
        await op.close()
    check(raised is not None and "append-only" in raised, "a privileged session without the teardown path is refused by "
                                                          "the trigger", f"{raised}")


async def y_offering(conn):
    section("[Y] Offering rules (DRAFT) — 506(b) teaser by relationship date; 506(c) third-party verification; "
            "not_set unchanged")
    t = today()
    b, c, pl = S["deals"]["b506"], S["deals"]["c506"], S["deals"]["plain"]
    await platform(conn, "UPDATE deals SET published_at = $2 WHERE id = $1 AND system_to IS NULL", UUID(b),
                   datetime.combine(t - timedelta(days=10), datetime.min.time(), tzinfo=timezone.utc), fetch="execute")
    c_i, _ = await api(INV1, "PUT", f"/api/v1/deal-room/relationship-dates/{ENTITY[INV1]}",
                       {"established_on": (t - timedelta(days=400)).isoformat(), "basis": "client since"})
    c_s, _ = await api(U_STAFF, "PUT", f"/api/v1/deal-room/relationship-dates/{ENTITY[INV1]}",
                       {"established_on": (t - timedelta(days=400)).isoformat(), "basis": "client since"})
    c_s4, _ = await api(U_STAFF, "PUT", f"/api/v1/deal-room/relationship-dates/{ENTITY[INV4]}",
                        {"established_on": (t - timedelta(days=2)).isoformat(), "basis": "met at conference"})
    check(c_i == 403 and c_s == 200 and c_s4 == 200, "relationship dates: investor 403, staff 200 (identical request)",
          f"{c_i}/{c_s}/{c_s4}")
    _, l1 = await api(INV1, "GET", "/api/v1/deals?limit=200")
    _, l4 = await api(INV4, "GET", "/api/v1/deals?limit=200")
    ids1 = {d["id"] for d in (l1 if isinstance(l1, list) else [])}
    ids4 = {d["id"] for d in (l4 if isinstance(l4, list) else [])}
    check(b in ids1 and b not in ids4 and pl in ids4, "marketplace list: the 506(b) deal shows to the earlier "
                                                      "relationship only; a not_set deal shows to both",
          f"{b in ids1} {b in ids4} {pl in ids4}")
    g1, _ = await api(INV1, "GET", f"/api/v1/deals/{b}")
    g4, _ = await api(INV4, "GET", f"/api/v1/deals/{b}")
    r1, _ = await room(INV1, b)
    r4, _ = await room(INV4, b)
    s1 = (await search(INV1, "dr3bteaser"))[1]
    s4 = (await search(INV4, "dr3bteaser"))[1]
    h1 = any(r.get("parent_id") == b for r in s1.get("results", []))
    h4 = any(r.get("parent_id") == b for r in s4.get("results", []))
    check((g1, r1, h1) == (200, 200, True) and (g4, r4, h4) == (404, 404, False),
          "deal page, room and Document Search agree: before publication → shown; after → hidden (404)",
          f"{g1} {r1} {h1} / {g4} {r4} {h4}")
    g2, _ = await api(INV2, "GET", f"/api/v1/deals/{b}")
    check(g2 == 404, "no recorded date: the entity's CRM created_at (today) is used — after publication, so hidden",
          f"{g2}")
    gp, _ = await api(U_PUB, "GET", f"/api/v1/deal-room/deals/{b}")
    check(gp == 200, "the publisher always reaches their own 506(b) deal", f"{gp}")
    c2, o2 = await request(INV2, c)
    c1, o1 = await request(INV1, c)
    check(c2 == 200 and o2.get("status") == "not_eligible" and any("506(c)" in m for m in o2.get("missing", [])),
          "506(c): self-certified is refused, naming the DRAFT rule", dumps(o2))
    check(c1 == 200 and o1.get("status") == "granted", "506(c): third-party verified is admitted", dumps(o1))
    cp, op_ = await request(INV2, pl)
    check(cp == 200 and op_.get("status") == "granted", "not_set: self-certified stays eligible (today's behaviour)",
          dumps(op_))
    _, env = await room(U_PUB, c)
    check("DRAFT" in (env.get("notices", {}).get("offering_draft") or "") and any(
        "DRAFT" in (o.get("label") or "") for o in env.get("vocabularies", {}).get("offering_types", [])),
          "the rules are labelled DRAFT, pending counsel review, in the envelope", dumps(env.get("notices")))


async def y_isolation(conn):
    section("[Y] Org isolation — both directions, under app_service (rolbypassrls = false)")
    bypass = await conn.fetchval("SELECT rolbypassrls FROM pg_roles WHERE rolname = current_user")
    check(bypass is False, "the isolation role does not bypass RLS", f"{bypass}")
    await scoped(conn, ORG_B, "INSERT INTO deal_room_activity (org_id, deal_id, user_id, action) VALUES ($1, $2, $3, "
                              "'room_open')", ORG_B, D_B, U_B_INV, fetch="execute")
    for t in ("deal_room_activity", "deal_room_watermarks", "deal_room_questions", "deal_room_advisors",
              "deal_room_return_notices", "crm_relationship_dates"):
        a_sees_a = await scoped(conn, ORG_A, f"SELECT count(*) FROM {t} WHERE org_id = $1", ORG_A, fetch="fetchval")
        b_sees_a = await scoped(conn, ORG_B, f"SELECT count(*) FROM {t} WHERE org_id = $1", ORG_A, fetch="fetchval")
        check(a_sees_a > 0 and b_sees_a == 0, f"{t}: org A's rows are visible to A ({a_sees_a}) and to B (0)",
              f"{a_sees_a}/{b_sees_a}")
    b_own = await scoped(conn, ORG_B, "SELECT count(*) FROM deal_room_activity WHERE org_id = $1", ORG_B, fetch="fetchval")
    a_sees_b = await scoped(conn, ORG_A, "SELECT count(*) FROM deal_room_activity WHERE org_id = $1", ORG_B,
                            fetch="fetchval")
    check(b_own == 1 and a_sees_b == 0, "the other direction: org B's activity is visible to B only", f"{b_own}/{a_sees_b}")
    raised = None
    try:
        await scoped(conn, ORG_B, "INSERT INTO deal_room_activity (org_id, deal_id, user_id, action) VALUES ($1, $2, $3, "
                                  "'room_open')", ORG_A, UUID(S["deals"]["wm"]), INV1, fetch="execute")
    except Exception as exc:  # noqa: BLE001
        raised = type(exc).__name__
    check(raised is not None, "writing an org-A row from org B's context is refused (RLS WITH CHECK)", f"{raised}")
    code, _ = await room(U_B_INV, S["deals"]["wm"])
    code2, _ = await api(U_B_ADMIN, "GET", f"/api/v1/deal-room/deals/{S['deals']['wm']}/activity")
    check(code == 404 and code2 == 404, "another firm's investor and org admin get 404 on org A's room and activity",
          f"{code} {code2}")


async def y_gates(conn):
    section("[Y] Gates both ways on identical requests (the remaining new manager routes)")
    wm = S["deals"]["wm"]
    for label, method, path, body in (
            ("settings", "PUT", f"/api/v1/deal-room/deals/{wm}/settings", {"watermark": "on"}),
            ("answer", "POST", f"/api/v1/deal-room/deals/{S['deals']['qa']}/questions/{S['q']['q02']}/answer",
             {"body": "Yes, final."}),
            ("advisor list (manager scope)", "GET", f"/api/v1/deal-room/deals/{wm}/advisors", None)):
        c_out, _ = await api(U_PLAIN, method, path, body)
        c_in, _ = await api(U_PUB, method, path, body)
        want_out = (403, 404) if method != "GET" else (200, 403, 404)
        check(c_out in want_out and c_in == 200, f"{label}: a plain member refused, the publisher admitted",
              f"{c_out}/{c_in}")
    _, lst = await api(U_PLAIN, "GET", f"/api/v1/deal-room/deals/{wm}/advisors")
    check(not lst.get("rows"), "a member with no access lists no one's advisors", dumps(lst))


async def y_routes(conn):
    section("[Y] Every new route gated and in AUTHZ1_ROUTES.json (--check CURRENT); no existing gate changed")
    before = json.loads(git_show(BEFORE_REF, "docs/AUTHZ1_ROUTES.json") or "{}")
    now = json.loads((REPO / "docs" / "AUTHZ1_ROUTES.json").read_text(encoding="utf-8"))

    def gates(d):
        return {(tuple(r.get("methods") or []), r["path"]): r for r in d.get("routes", [])}

    gb, gn = gates(before), gates(now)
    changed = [k for k in gb if k in gn and gb[k].get("after") != gn[k].get("after")]
    removed = [k for k in gb if k not in gn]
    check(len(gb) > 0 and not changed and not removed, "no existing route's gate changed or vanished",
          f"{changed[:5]} {removed[:5]}")
    new = [k for k in gn if k not in gb]
    check(len(new) == 19 and all(k[1].startswith("/api/v1/deal-room/") for k in new),
          "19 new routes, all on the /deal-room surface", f"{len(new)}: {[k[1] for k in new][:4]}")
    ungated = [k for k in new if gn[k]["after"] == "none beyond auth"]
    check(not ungated, "every new route names its gate", f"{ungated}")
    code, out, err = run_cmd([sys.executable, str(HERE / "authz1_routes.py"), "--check"], REPO, timeout=600)
    check(code == 0 and "CURRENT" in out, "authz1_routes.py --check: CURRENT", (out + err)[-200:])


async def y_front_end(conn):
    section("[Y] Front end — node tests (manager controls, view-only, advisors, Q&A); node --test tests/; npm run build")
    code, out, err = run_cmd(["node", "--test", "tests/dealroom/"], WEB_DIR, timeout=300)
    check(code == 0 and re.search(r"^# fail 0", out, re.M) is not None, "node --test tests/dealroom passes",
          (out + err)[-300:] if code else "")
    for name in (
            "view-only: no download control renders; View opens the page viewer; the notice says screenshots cannot "
            "be stopped",
            "watermark on: download renders only when can_download; a blocked type shows its reason",
            "manager controls (settings rows, share unwatermarked, activity, Q&A answer) only with can_write === true",
            "Q&A: Ask only with can_ask; investors never see an asker's name; manager actions only with can_write",
            "expired / paused: no folders open, the reason shows, signatures stay",
            "advisor: acknowledgement first; no sign / request / ask / invite controls",
            "grid: access line, advisors under the investor, Extend + return-or-destroy only from the envelope's "
            "actions",
            "my deal access: end date, paused reason, advisors, return-or-destroy notice, deals I advise on"):
        check(re.search(rf"^ok \d+ - {re.escape(name)}", out, re.M) is not None, f"node test: {name}")
    code, out, err = run_cmd(["node", "--test", "tests/"], WEB_DIR, timeout=900)
    check(code == 0 and re.search(r"^# fail 0", out, re.M) is not None, "node --test tests/ passes (0 fail)",
          (out + err)[-300:] if code else "")
    code, out, err = run_cmd(["npm", "run", "build"], WEB_DIR, timeout=1800)
    check(code == 0, "npm run build exits 0", (out + err)[-400:] if code else "")


def regression():
    section("[Y] Regression — verify_dealroom2.py ONCE, flat subprocess AFTER teardown, VERIFY_NESTED=1, 2700 s")
    env = os.environ | {"VERIFY_NESTED": "1"}
    code, out, err = run_cmd([sys.executable, str(HERE / "verify_dealroom2.py")], REPO, timeout=2700, env=env)
    m = re.findall(r"^TOTAL: (\d+) PASS, (\d+) FAIL", out, re.M)
    fails = [ln for ln in out.splitlines() if ln.startswith("[FAIL]")]
    check(bool(m) and m[-1][1] == "0" and code == 0, "verify_dealroom2.py: 0 FAIL",
          f"{m[-1] if m else 'no TOTAL'}; {fails[:3]} {err[-200:] if not m else ''}")


async def run(name, fn, *args):
    try:
        res = fn(*args)
        if asyncio.iscoroutine(res):
            await res
    except Exception as exc:  # noqa: BLE001
        check(False, f"{name} raised", f"{type(exc).__name__}: {exc}")
        traceback.print_exc()


async def cleanup_only(conn) -> int:
    t0 = time.monotonic()
    log: list[str] = []
    try:
        log = await teardown()
    except Exception as exc:  # noqa: BLE001
        check(False, "cleanup raised", f"{type(exc).__name__}: {exc}")
    left = await leftovers()
    print(f"CLEANUP_SECONDS: {time.monotonic() - t0:.1f}")
    print(f"LEFTOVERS: {left}")
    print(f"[INFO] cleanup removed: {log}")
    await conn.close()
    check(left == 0, "--cleanup-only: zero fixture rows remain", f"{left}")
    print(f"\nTOTAL: {_n_pass} PASS, {_n_fail} FAIL")
    return 0 if _n_fail == 0 else 1


async def main_async() -> int:
    dsn, how = await admin_dsn()
    if not dsn:
        print(f"[FAIL] no working DATABASE_URL: {how}")
        print("TOTAL: 0 PASS, 1 FAIL")
        return 1
    os.environ["DATABASE_URL"] = dsn
    print(f"[INFO] database via {how}")
    conn = await connect(dsn)
    S["conn"] = conn
    S["subs"] = {}
    install_stubs()
    if "--cleanup-only" in sys.argv:
        return await cleanup_only(conn)
    pre = real = None
    try:
        if not await y_prefix(conn):
            print(f"\nTOTAL: {_n_pass} PASS, {_n_fail} FAIL")
            return 1
        log = await teardown()
        if log:
            find("pre-run cleanup removed an earlier run's leftovers", "; ".join(log))
        check(await leftovers() == 0, "pre-run cleanup: zero fixture rows before seeding")
        pre = await counts(conn)
        real = await real_fingerprints(conn)
        await run("task 1", y_task1, conn)
        await run("schema", y_schema, conn)
        await run("constraints", y_constraints, conn)
        await run("seed", seed, conn)
        await run("setup", y_setup, conn)
        if S.get("deals") and all(S["deals"].values()) and all(S.get("docs", {}).values()):
            await run("watermark", y_watermark, conn)
            await run("view-only", y_view_only, conn)
            await run("expiry", y_expiry, conn)
            await run("pause", y_pause, conn)
            await run("q&a", y_qa, conn)
            await run("advisors", y_advisors, conn)
            await run("revoke + notice", y_revoke_notice, conn)
            await run("activity", y_activity, conn)
            await run("offering rules", y_offering, conn)
            await run("isolation", y_isolation, conn)
            if S.get("q"):
                await run("gates", y_gates, conn)
        else:
            check(False, "setup created the fixture deals and documents", "every deal-scoped section skipped")
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
            log = await teardown()
            print(f"[INFO] teardown removed: {log}")
            left = await leftovers()
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
