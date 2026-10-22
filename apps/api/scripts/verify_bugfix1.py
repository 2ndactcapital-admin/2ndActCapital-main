"""verify_bugfix1 — the HollisMuse round 1–2 defects (BUGFIX1).

Pass/fail only, no prompts. Prints 'TOTAL: N PASS, M FAIL' and exits non-zero
on any failure. AUTHZ_TEST_MODE=1 is set before anything imports ``main``;
secrets are hydrated from Doppler over HTTPS (``_db_connect``) before ``main``
is imported. SEC and R2 are mocked (httpx.MockTransport; storage functions
replaced by recorders that fail the run if a real call is attempted).

Everything after the bearer token is the real app: the token is mapped to
fixture claims (``main.verify_token``), then middleware, Principal, every gate
and every handler run for real against the live database.

Fixtures live in two fixture orgs (99000000-…-0000000bf101 / bf102), plus ONE
fixture super-admin user in the default org (2nd Act) — the caller that
restores TEST-Submit Test through the new Un-archive endpoint. Every fixture
user is created explicitly with the auth0_sub its token carries and
``is_test = true``. Teardown runs at the start and the end, FK
child-before-parent from pg_constraint, then every counted table is re-read
for its exact pre-test count (plus exactly the rows the reported real repairs
wrote — each one printed).

REAL RECORDS this script changes (the only ones; each reported):
  * the Task-1-proven repairs in scripts/_bugfix1_repairs.py — every live
    deal's deal_stage mirror (system-axis restatement) and the duplicate
    current tax-ID row(s) (valid-axis close);
  * TEST-Submit Test (bed53061-…) restored through POST /deals/{id}/unarchive.
Real ``portfolio.note_gold_values`` must be unchanged (count + checksum).

With VERIFY_NESTED=1 the regression section prints one '[SKIP] nested run'.
"""

from __future__ import annotations

import os as _authz_os
_authz_os.environ["AUTHZ_TEST_MODE"] = "1"  # AUTHZ1: set before main is imported

import asyncio
import json
import os
import pathlib
import re
import subprocess
import sys
import traceback
from datetime import date
from types import SimpleNamespace
from uuid import UUID

HERE = pathlib.Path(__file__).resolve().parent
API_DIR = HERE.parent
REPO = API_DIR.parents[1]
WEB_DIR = REPO / "apps" / "web"
sys.path.insert(0, str(HERE))

from _db_connect import admin_dsn, connect, hydrate  # noqa: E402

hydrate()  # BEFORE main is imported (Settings is lru_cached)
os.environ.setdefault("EDGAR_USER_AGENT", "bugfix1-verify verify@test.local")

import httpx  # noqa: E402

import _authz1_common as C  # noqa: E402

BEFORE_REF = "e816797"   # bugfix1-work base (usermgmt1 merged) — the pre-sprint code
TAG = "bugfix1verify"
TOKEN = "bf1tok7q"       # fixture search token (no real document carries it)
TEXT_TOKEN = "bf1txt9z"  # only inside a fixture deal attachment's extracted text

ORG_A = UUID("99000000-0000-0000-0000-0000000bf101")
ORG_B = UUID("99000000-0000-0000-0000-0000000bf102")
FIXTURE_ORGS = [ORG_A, ORG_B]
DEFAULT_ORG = UUID("00000000-0000-0000-0000-000000000001")
TEST_SUBMIT = UUID("bed53061-fe10-4771-81fc-21f262d65caf")


def _id(n: int) -> UUID:
    return UUID(f"99000000-0000-0000-0000-0000000bf{n:03d}")


# users
U_SUPER, U_STAFF, U_ORGADMIN, U_MEMBER, U_MEMBER2, U_OTHER, U_NOROLE, U_RESTORER = (
    _id(110), _id(111), _id(112), _id(113), _id(114), _id(115), _id(116), _id(117))
USERS = {
    U_SUPER: (ORG_A, "super_admin", "super"), U_STAFF: (ORG_A, "member", "staff"),
    U_ORGADMIN: (ORG_A, "org_admin", "orgadmin"), U_MEMBER: (ORG_A, "member", "member"),
    U_MEMBER2: (ORG_A, "member", "member2"), U_OTHER: (ORG_B, "member", "other"),
    U_NOROLE: (ORG_A, "member", "norole"), U_RESTORER: (DEFAULT_ORG, "super_admin", "restorer"),
}
# entities
E_MINE, E_OTHER, E_RESTR, E_LLC, E_TRUST, E_B = (_id(120), _id(121), _id(122), _id(123), _id(124), _id(125))
# deals: one per lifecycle state in org A, plus the subjects of the action tests
STATUSES = ["draft", "submitted", "under_review", "active", "closed", "archived"]
D_BY_STATUS = {s: _id(130 + i) for i, s in enumerate(STATUSES)}
D_ACTIVE = D_BY_STATUS["active"]
D_DRAFT = D_BY_STATUS["draft"]
D_SUBMIT_MISSING = _id(137)   # draft lacking target_raise
D_ARCHIVE_ME = _id(138)       # under_review → archived → un-archived
D_B = _id(139)                # org B deal
# SPVs
S1, S_RESTR = _id(140), _id(141)
# attachments
DD_ACTIVE, DD_DRAFT, DD_B, ED_MINE, ED_RESTR, ED_OTHER, SD_S1, SD_RESTR = (
    _id(150), _id(151), _id(152), _id(153), _id(154), _id(155), _id(156), _id(157))
# portfolio
A_FUND, P_MINE, P_OTHER, CM_MINE, CM_OTHER = _id(160), _id(161), _id(162), _id(163), _id(164)

COUNTED = ("public.users", "public.organizations", "public.deals", "public.audit_log",
           "public.entity_tax_ids", "public.config", "public.member_todos", "public.deal_votes",
           "public.member_investments", "public.spvs", "public.spv_subscriptions", "public.spv_documents",
           "public.deal_documents", "public.entity_documents", "public.entities", "public.entity_ownership",
           "public.roles", "public.user_roles", "public.delegate_grants", "public.staff_assignments",
           "portfolio.commitments", "portfolio.positions", "portfolio.assets")

_n_pass = 0
_n_fail = 0
REPAIR_DELTAS: dict[str, int] = {}   # rows the REAL repairs wrote, per table (reported)


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


# ═══ DB helpers (verify's own connection; RLS context set explicitly) ═══════
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


async def teardown(conn) -> list[str]:
    """Fixture rows only, by tag / fixture org / fixture id. Never a TRUNCATE.
    Deal history versions of FIXTURE deals carry a fixture org, so they go with
    the org; the real repairs' rows (reported) are NOT fixtures and stay."""
    log: list[str] = []
    users = list(USERS)
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        # The audit row of the REAL TEST-Submit restore stays (it records a real
        # change); its actor was the fixture super admin, whose row is deleted
        # below (audit_log.user_id has an FK), so the actor moves into the payload.
        await conn.execute(
            "UPDATE audit_log SET user_id = NULL, payload = COALESCE(payload, '{}'::jsonb) || "
            "jsonb_build_object('actor', 'verify_bugfix1 fixture super admin (deleted at teardown)') "
            "WHERE user_id = $1 AND resource_id = $2", U_RESTORER, TEST_SUBMIT)
        await conn.execute("DELETE FROM audit_log WHERE org_id = ANY($1::uuid[]) OR user_id = ANY($2::uuid[])",
                           FIXTURE_ORGS, users)
        await conn.execute("DELETE FROM config WHERE org_id = ANY($1::uuid[])", FIXTURE_ORGS)
        await conn.execute("DELETE FROM member_todos WHERE user_id = ANY($1::uuid[]) OR org_id = ANY($2::uuid[])",
                           users, FIXTURE_ORGS)
        for t in ("portfolio.commitments", "portfolio.positions", "portfolio.assets"):
            await delete_tree(conn, t, "org_id = ANY($1::uuid[])", [FIXTURE_ORGS], log=log)
        # Guard: the FK-recursive delete would take a REAL deal with it if one
        # pointed at a fixture user. Refuse instead.
        real_refs = await conn.fetchval(
            "SELECT count(*) FROM deals WHERE NOT (org_id = ANY($1::uuid[])) AND (created_by = ANY($2::uuid[]) "
            "OR submitted_by = ANY($2::uuid[]) OR reviewed_by = ANY($2::uuid[]))", FIXTURE_ORGS, users)
        if real_refs:
            raise RuntimeError(f"{real_refs} real deal(s) reference a fixture user — refusing to delete it")
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
        ("SELECT count(*) FROM config WHERE org_id = ANY($1::uuid[])", [FIXTURE_ORGS]),
        ("SELECT count(*) FROM audit_log WHERE org_id = ANY($1::uuid[])", [FIXTURE_ORGS]),
        ("SELECT count(*) FROM portfolio.commitments WHERE org_id = ANY($1::uuid[])", [FIXTURE_ORGS]),
    ):
        n += int(await platform(conn, sql, *args, fetch="fetchval"))
    return n


# ═══ Seed ═══════════════════════════════════════════════════════════════════
async def seed(conn) -> None:
    from services import rbac

    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        for org, nm in ((ORG_A, "a"), (ORG_B, "b")):
            await conn.execute("INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3)",
                               org, f"BUGFIX1 Verify Org {nm.upper()}", f"{TAG}-{nm}")
        perm_ids = {r["name"]: r["id"] for r in await conn.fetch("SELECT id, name FROM permissions")}
        roles = {}
        for org, rname in ((ORG_A, "member"), (ORG_A, "admin"), (ORG_A, "investment_staff"),
                           (ORG_A, "super_admin"), (ORG_B, "member")):
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
                uid, org, email_of(uid), f"BUGFIX1 {nm.title()}", sub_of(uid), account_role)
        for uid, rname in ((U_SUPER, "super_admin"), (U_STAFF, "investment_staff"), (U_ORGADMIN, "admin"),
                           (U_MEMBER, "member"), (U_MEMBER2, "member")):
            await conn.execute("INSERT INTO user_roles (user_id, role_id) VALUES ($1, $2)", uid, roles[(ORG_A, rname)])
        await conn.execute("INSERT INTO user_roles (user_id, role_id) VALUES ($1, $2)", U_OTHER, roles[(ORG_B, "member")])
        await rbac.grant_org_admin(conn, U_ORGADMIN, ORG_A)

        # the deal_stages config (labels), in lifecycle order; one investment stage
        for i, label in enumerate(["Sourced", "Submitted", "Under Review", "Active", "Closed", "Archived"]):
            await conn.execute(
                "INSERT INTO config (org_id, config_key, config_value, category, display_order) "
                "VALUES ($1, $2, $3, 'deal_stages', $4)", ORG_A, f"deal_stage_{i + 1}", label, i + 1)
        await conn.execute("INSERT INTO config (org_id, config_key, config_value, category, display_order) "
                           "VALUES ($1, 'investment_stage_1', 'Interest Indicated', 'investment_stages', 1)", ORG_A)

        # entities
        for eid, org, etype, name, restricted in (
            (E_MINE, ORG_A, "individual", "BF1 Mine", False), (E_OTHER, ORG_A, "individual", "BF1 Other", False),
            (E_RESTR, ORG_A, "individual", "BF1 Restricted", True), (E_LLC, ORG_A, "llc", "BF1 Capital LLC", False),
            (E_TRUST, ORG_A, "trust", "BF1 Family Trust", False), (E_B, ORG_B, "individual", "BF1 Org B", False),
        ):
            await conn.execute("INSERT INTO entities (id, org_id, entity_type, display_name, access_restricted) "
                               "VALUES ($1, $2, $3::entity_type, $4, $5)", eid, org, etype, name, restricted)
        # CRM-02 fixture: the Hargrove shape — Mine owns 40% of the LLC, the Trust 60%
        await conn.execute("INSERT INTO entity_ownership (org_id, parent_id, child_id, ownership_pct) VALUES "
                           "($1, $2, $4, 40), ($1, $3, $4, 60)", ORG_A, E_MINE, E_TRUST, E_LLC)
        # visibility: the member is a delegate of E_MINE; staff is assigned the four open entities
        await conn.execute("INSERT INTO delegate_grants (org_id, principal_entity_id, delegate_user_id, scope) "
                           "VALUES ($1, $2, $3, 'view_only')", ORG_A, E_MINE, U_MEMBER)
        for eid in (E_MINE, E_OTHER, E_LLC, E_TRUST, E_RESTR):
            await conn.execute("INSERT INTO staff_assignments (org_id, entity_id, assigned_to_user_id) "
                               "VALUES ($1, $2, $3)", ORG_A, eid, U_STAFF)

        # deals — the OLD shape: deal_stage 'sourced' whatever the status
        for st, did in D_BY_STATUS.items():
            await conn.execute(
                "INSERT INTO deals (id, org_id, name, slug, deal_status, deal_stage, description, "
                "asset_super_class, target_raise, minimum_investment) "
                "VALUES ($1, $2, $3, $4, $5::deal_status, 'sourced', 'd', 'taxonomy_sc_1', 1000000, 25000)",
                did, ORG_A, f"BF1 Deal {st}", f"{TAG}-{st}", st)
        await conn.execute(
            "INSERT INTO deals (id, org_id, name, slug, deal_status, description) "
            "VALUES ($1, $2, 'BF1 Missing Fields', $3, 'draft', 'd')", D_SUBMIT_MISSING, ORG_A, f"{TAG}-missing")
        await conn.execute(
            "INSERT INTO deals (id, org_id, name, slug, deal_status, deal_stage) "
            "VALUES ($1, $2, 'BF1 Archive Me', $3, 'under_review', 'sourced')", D_ARCHIVE_ME, ORG_A, f"{TAG}-arch")
        await conn.execute("INSERT INTO deals (id, org_id, name, slug, deal_status) VALUES ($1, $2, 'BF1 Org B Deal', "
                           "$3, 'active')", D_B, ORG_B, f"{TAG}-b")

        # member investments + votes
        await conn.execute("INSERT INTO member_investments (org_id, deal_id, user_id, investment_stage) "
                           "VALUES ($1, $2, $3, 'investment_stage_1')", ORG_A, D_ACTIVE, U_MEMBER)
        await conn.execute("INSERT INTO member_investments (org_id, deal_id, user_id) VALUES ($1, $2, $3)",
                           ORG_B, D_B, U_OTHER)
        await conn.execute("INSERT INTO deal_votes (org_id, deal_id, user_id, vote) VALUES "
                           "($1, $2, $3, 1), ($1, $2, $4, 1)", ORG_A, D_ACTIVE, U_MEMBER, U_MEMBER2)

        # SPVs: $1.2M + $140k soft vs $1M target; created then opened (two history rows)
        await conn.execute(
            "INSERT INTO spvs (id, org_id, deal_id, name, spv_status, target_raise, vehicle_entity_id, class_label) "
            "VALUES ($1, $2, $3, 'BF1 SPV', 'open', 1000000, $4, 'A'), "
            "($5, $2, $3, 'BF1 Restricted SPV', 'open', 500000, $6, 'B')",
            S1, ORG_A, D_ACTIVE, E_MINE, S_RESTR, E_RESTR)
        # spv_subs_active_unique allows ONE active subscription per (spv, entity),
        # so the $1,340,000 is two subscribers: Mine $1.2M + Other $140k.
        await conn.execute("INSERT INTO spv_subscriptions (org_id, spv_id, entity_id, commitment_amount) VALUES "
                           "($1, $2, $3, 1200000), ($1, $2, $4, 140000)", ORG_A, S1, E_MINE, E_OTHER)
        await conn.execute("INSERT INTO spv_status_history (org_id, spv_id, from_status, to_status, changed_at) VALUES "
                           "($1, $2, NULL, 'forming', now() - interval '1 hour'), ($1, $2, 'forming', 'open', now())",
                           ORG_A, S1)

        # attachments carrying the search token
        await conn.execute(
            "INSERT INTO deal_documents (id, org_id, deal_id, file_name, file_type, r2_key, r2_bucket, document_type, "
            "status, visible_to_members, extracted_data) VALUES "
            "($1, $2, $3, $4, 'text/plain', 'verify/none', 'verify-bucket', 'memo', 'approved', true, $5::jsonb), "
            "($6, $2, $7, $8, 'text/plain', 'verify/none', 'verify-bucket', 'memo', 'approved', true, NULL)",
            DD_ACTIVE, ORG_A, D_ACTIVE, f"{TOKEN}-deal.txt", json.dumps({"summary": f"contains {TEXT_TOKEN}"}),
            DD_DRAFT, D_DRAFT, f"{TOKEN}-draftdeal.txt")
        await conn.execute(
            "INSERT INTO deal_documents (id, org_id, deal_id, file_name, file_type, r2_key, r2_bucket, status, "
            "visible_to_members) VALUES ($1, $2, $3, $4, 'text/plain', 'verify/none', 'verify-bucket', 'approved', true)",
            DD_B, ORG_B, D_B, f"{TOKEN}-orgb.txt")
        for did, eid, title in ((ED_MINE, E_MINE, f"{TOKEN}-entity.pdf"), (ED_RESTR, E_RESTR, f"{TOKEN}-restricted.pdf"),
                                (ED_OTHER, E_OTHER, f"{TOKEN}-other.pdf")):
            await conn.execute("INSERT INTO entity_documents (id, org_id, entity_id, title, file_name, doc_category) "
                               "VALUES ($1, $2, $3, $4, $4, 'other')", did, ORG_A, eid, title)
        for did, sid, title in ((SD_S1, S1, f"{TOKEN}-spv.pdf"), (SD_RESTR, S_RESTR, f"{TOKEN}-restrspv.pdf")):
            await conn.execute("INSERT INTO spv_documents (id, org_id, spv_id, doc_type, title) "
                               "VALUES ($1, $2, $3, 'formation', $4)", did, ORG_A, sid, title)

        # dashboard to-dos: the legacy stub shape (no entity id) and the new one
        await conn.execute(
            "INSERT INTO member_todos (org_id, user_id, kind, category, source, title, priority, status) "
            "VALUES ($1, $2, 'actual', 'crm', 'entity_stub', 'Complete profile: BF1 Stub', 5, 'open')",
            ORG_A, U_STAFF)
        await conn.execute(
            "INSERT INTO member_todos (org_id, user_id, kind, category, source, title, priority, status, "
            "related_type, related_id) VALUES ($1, $2, 'actual', 'crm', 'entity_stub', 'Complete profile: BF1 Mine', "
            "4, 'open', 'entity', $3)", ORG_A, U_STAFF, E_MINE)

        # commitments: one on the member's entity, one on another entity
        await conn.execute("INSERT INTO portfolio.assets (id, org_id, name, asset_type) "
                           "VALUES ($1, $2, 'BF1 Fund III', 'fund')", A_FUND, ORG_A)
        for pid, eid in ((P_MINE, E_MINE), (P_OTHER, E_OTHER)):
            await conn.execute(
                "INSERT INTO portfolio.positions (id, org_id, owner_entity_id, asset_id, as_of_date, authority, "
                "source_system) VALUES ($1, $2, $3, $4, current_date, 'manual', 'manual')", pid, ORG_A, eid, A_FUND)
        for cid, pid in ((CM_MINE, P_MINE), (CM_OTHER, P_OTHER)):
            await conn.execute(
                "INSERT INTO portfolio.commitments (id, org_id, position_id, commitment_amount, called_to_date, "
                "unfunded) VALUES ($1, $2, $3, 1000000, 250000, 750000)", cid, ORG_A, pid)


# ═══ The real app, token stubbed by bearer string ══════════════════════════
CLAIMS_BY_TOKEN: dict[str, dict] = {}
R2_CALLS: list = []


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
    from services import storage
    from services import users as users_mod

    main.verify_token = lambda tok: dict(CLAIMS_BY_TOKEN[tok])
    # main's lifespan fills the dashboard brief registry; httpx.ASGITransport
    # never runs the lifespan, so without this /dashboard/brief has no blocks.
    from services.brief_blocks import register_brief_blocks

    register_brief_blocks()

    async def no_profile(request, c):
        return None

    users_mod.fetch_auth0_profile = no_profile

    def no_r2(*a, **k):
        R2_CALLS.append((a, k))
        raise AssertionError("verify: real R2 must never be called")

    storage.get_s3_client = no_r2
    storage.upload_bytes = no_r2
    storage.delete_object = no_r2


def dumps(x) -> str:
    return json.dumps(x, ensure_ascii=False, default=str)[:400]


# ═══ Sections ══════════════════════════════════════════════════════════════
async def y_task1(conn):
    section("[Y] Task 1 — findings 1a–1f reported, each with its file and query")
    doc = REPO / "docs" / "BUGFIX1_DISCOVERY.md"
    txt = doc.read_text(encoding="utf-8") if doc.exists() else ""
    for key, needles in {
        "1a": ("routers/spv.py", "Math.min(100", "spv_status_history"),
        "1b": ("deal_status", "deal_stage", "deal_stages"),
        "1c": ("deal_documents", "entity_documents", "spv_documents"),
        "1d": ("entity_ownership", "entity_relationships", "entity_tax_ids"),
        "1e": ("services/edgar_bulk.py", "peek_range_ignored", "e3fde9c0"),
        "1f": ("fee-schedules",),
    }.items():
        m = re.search(rf"^## {key}\b.*?(?=^## |\Z)", txt, re.M | re.S)
        body = m.group(0) if m else ""
        check(bool(m) and all(n in body for n in needles),
              f"{key} reported in docs/BUGFIX1_DISCOVERY.md with its file and query", f"needs {needles}")
    run_e3 = await platform(conn, "SELECT details->'bulk_result'->>'peek_range_ignored' AS ign, "
                                  "details->'bulk_result'->>'peek_skipped' AS skp, sec_requests "
                                  "FROM portfolio.edgar_pipeline_runs WHERE id::text LIKE 'e3fde9c0%'", fetch="fetchrow")
    if run_e3:
        find("1e: run e3fde9c0 — every peek came back 200 (Range ignored), so no peek decision ran",
             f"peek_range_ignored={run_e3['ign']}, peek_skipped={run_e3['skp']}, sec_requests={run_e3['sec_requests']}")


async def y_spv(conn):
    section("[Y] SPV-01 — one committed / % of target function on all three surfaces (134%)")
    # BEFORE: reproduce the two old paths on the fixture.
    old_list_sql = re.search(r'SPV_SELECT = \((.*?)\)', git_show(BEFORE_REF, "apps/api/routers/spv.py"), re.S)
    old_cols = old_list_sql.group(1) if old_list_sql else ""
    check("total_committed" not in old_cols and "spv_status" in old_cols,
          "BEFORE: the list endpoint's SELECT had no committed column (card read undefined → $0 / 0%)")
    total = await scoped(conn, ORG_A, "SELECT sum(commitment_amount) FROM spv_subscriptions WHERE spv_id = $1 "
                                      "AND valid_to IS NULL", S1, fetch="fetchval")
    total = float(total) if total is not None else 0.0   # None = no fixture subscriptions (seed failed)
    old_cap_pct = min(100, round(total / 1000000 * 100))
    check(total == 1340000.0 and old_cap_pct == 100,
          "BEFORE: the cap table's Math.min(100, …) showed 100% for $1,340,000 of $1,000,000",
          f"{total} → {old_cap_pct}%")
    old_tab = git_show(BEFORE_REF, "apps/web/components/spv/SPVSubscriptionsTab.jsx")
    check("Math.min(100" in old_tab, "BEFORE: the cap-table component capped the percentage at 100")

    code_l, lst = await api(U_STAFF, "GET", "/api/v1/spvs")
    card = next((s for s in lst if s.get("id") == str(S1)), {}) if isinstance(lst, list) else {}
    code_c, cap = await api(U_STAFF, "GET", f"/api/v1/spvs/{S1}/captable")
    code_d, brief = await api(U_STAFF, "GET", "/api/v1/dashboard/brief")
    blocks = {b["key"]: b for b in (brief or {}).get("blocks", [])} if isinstance(brief, dict) else {}
    act = (blocks.get("spv_activity") or {}).get("data") or {}
    dash = next((s for s in act.get("spvs", []) if s.get("spv_id") == str(S1)), {})
    cc, kc, dc = card.get("commitment") or {}, (cap or {}).get("commitment") or {}, dash.get("commitment") or {}
    check(code_l == 200 and cc.get("pct_of_target") == 134 and cc.get("total_committed") == 1340000.0,
          "AFTER: SPV list card → $1,340,000 / 134%", dumps(cc))
    check(code_c == 200 and kc.get("pct_of_target") == 134 and kc.get("over_target") is True,
          "AFTER: cap table → 134% with over_target", dumps(kc))
    check(code_d == 200 and dc.get("pct_of_target") == 134, "AFTER: dashboard SPV card → 134%", dumps(dc))
    strip = lambda d: {k: d.get(k) for k in ("total_committed", "soft_committed", "hard_committed",
                                             "pct_of_target", "over_target", "labels")}
    check(bool(cc) and strip(cc) == strip(kc) == strip(dc),
          "the three surfaces carry the IDENTICAL summary (one function, soft/hard labels included)")
    check(cc.get("soft_committed") == 1340000.0 and cc.get("hard_committed") == 0.0
          and bool((cc.get("labels") or {}).get("soft")), "soft vs hard split and labelled the same everywhere")
    # member: same summary on the list, captable refused (gate unchanged)
    code_m, mlst = await api(U_MEMBER, "GET", "/api/v1/spvs")
    mcard = next((s for s in mlst if s.get("id") == str(S1)), {}) if isinstance(mlst, list) else {}
    check(code_m == 200 and (mcard.get("commitment") or {}).get("pct_of_target") == 134,
          "a member sees the same 134% on the list card")
    code_mc, _ = await api(U_MEMBER, "GET", f"/api/v1/spvs/{S1}/captable")
    check(code_mc == 403, "the member is still refused the cap table (manage_deals)", f"HTTP {code_mc}")

    section("[Y] DASH-03 — one line per SPV, its current state")
    old = await scoped(conn, ORG_A, "SELECT s.name FROM spv_status_history ssh JOIN spvs s ON s.id = ssh.spv_id "
                                    "WHERE ssh.org_id = $1 ORDER BY ssh.changed_at DESC LIMIT 5", ORG_A)
    check(sum(1 for r in old if r["name"] == "BF1 SPV") == 2,
          "BEFORE: the old event query lists the fixture SPV twice (created + opened)")
    lines = [s for s in act.get("spvs", []) if s.get("spv_id") == str(S1)]
    check(len(lines) == 1 and lines[0].get("status") == "open", "AFTER: one line, current state 'open'",
          dumps(lines))
    ids = [s.get("spv_id") for s in act.get("spvs", [])]
    check(len(ids) == len(set(ids)) and len(ids) > 0, "no SPV appears twice on the card", f"{ids}")
    check(act.get("soft_subscriptions") == 2, "soft subscriptions counted from the same summaries (2)",
          f"{act.get('soft_subscriptions')}")


async def y_lifecycle(conn):
    from services import deal_lifecycle as dlc

    section("[Y] MKT-01 / DASH-01 — one lifecycle: badge, Stage column, summary, dashboard agree")
    old_rows = await scoped(conn, ORG_A, "SELECT LOWER(COALESCE(deal_stage, 'sourced')) AS stage, count(*) n "
                                         "FROM deals WHERE org_id = $1 AND valid_to IS NULL AND system_to IS NULL "
                                         "GROUP BY 1", ORG_A)
    check(len(old_rows) == 1 and old_rows[0]["stage"] == "sourced",
          "BEFORE: the old summary query puts every fixture deal (six states) in ONE 'sourced' bucket",
          dumps([dict(r) for r in old_rows]))
    labels = {"draft": "Sourced", "submitted": "Submitted", "under_review": "Under Review", "active": "Active",
              "closed": "Closed", "archived": "Archived"}
    code, deals = await api(U_STAFF, "GET", "/api/v1/deals?limit=200")
    by_id = {d["id"]: d for d in deals} if isinstance(deals, list) else {}
    ok_badges = all(by_id.get(str(did), {}).get("stage_label") == labels[st] for st, did in D_BY_STATUS.items())
    check(code == 200 and ok_badges, "badge / Stage column: every fixture deal's stage_label is its config label",
          dumps({st: by_id.get(str(did), {}).get("stage_label") for st, did in D_BY_STATUS.items()}))
    code_s, summ = await api(U_STAFF, "GET", "/api/v1/deals/stage-summary")
    sql = {r["s"]: r["n"] for r in await scoped(
        conn, ORG_A, "SELECT deal_status::text s, count(*) n FROM deals WHERE org_id = $1 AND valid_to IS NULL "
                     "AND system_to IS NULL GROUP BY 1", ORG_A)}
    api_counts = {r["status"]: r["count"] for r in summ} if isinstance(summ, list) else {}
    check(code_s == 200 and api_counts == {s: sql.get(s, 0) for s in STATUSES} and len(summ) == 6,
          "Marketplace summary: one row per lifecycle state, counts equal direct SQL", dumps(summ))
    check([r["label"] for r in summ] == [labels[s] for s in STATUSES] if isinstance(summ, list) else False,
          "summary labels and order come from the deal_stages config")
    code_b, brief = await api(U_STAFF, "GET", "/api/v1/dashboard/brief")
    pipe = next((b["data"] for b in (brief or {}).get("blocks", []) if b["key"] == "pipeline_snapshot"), {})
    check(code_b == 200 and [(s["status"], s["label"], s["count"]) for s in pipe.get("stages", [])]
          == [(r["status"], r["label"], r["count"]) for r in summ],
          "dashboard snapshot == Marketplace summary (same function)")
    code_m, msumm = await api(U_MEMBER, "GET", "/api/v1/deals/stage-summary")
    check(code_m == 200 and {r["status"] for r in msumm} == set(dlc.MEMBER_VISIBLE_STATUSES),
          "a member's summary covers only the statuses a member may see", dumps(msumm))
    # The Stage filter resolves a config key to the lifecycle state.
    code_f, filt = await api(U_STAFF, "GET", "/api/v1/deals?deal_stage=deal_stage_4&limit=200")
    fids = {d["id"] for d in filt} if isinstance(filt, list) else set()
    check(code_f == 200 and str(D_ACTIVE) in fids and str(D_DRAFT) not in fids,
          "Stage filter deal_stage_4 returns the Active deal and not the draft")

    section("[Y] Real deals — lifecycle mirror repaired (each reported, bi-temporal)")
    import _bugfix1_repairs as R

    plan = await R.deal_mirror_plan(conn)
    real_plan = [p for p in plan if UUID(p["org_id"]) not in FIXTURE_ORGS]
    for p in real_plan:
        print(f"[INFO] real deal {p['name']} ({p['deal_id']}): status {p['deal_status']}; "
              f"deal_stage {p['deal_stage_before']!r} -> {p['deal_stage_after']!r}")
    done = await R.apply_deal_mirror(conn, plan)
    REPAIR_DELTAS["public.deals"] = REPAIR_DELTAS.get("public.deals", 0) + len(real_plan)
    REPAIR_DELTAS["public.audit_log"] = REPAIR_DELTAS.get("public.audit_log", 0) + len(real_plan)
    left = [p for p in await R.deal_mirror_plan(conn) if UUID(p["org_id"]) not in FIXTURE_ORGS]
    check(len(left) == 0, "every real live deal's deal_stage now names its status's config key (re-read)",
          f"{len(real_plan)} restated, {len(left)} left")
    vers = await platform(conn, "SELECT count(*) FROM deals WHERE id = ANY($1::uuid[]) AND system_to IS NOT NULL",
                          [UUID(d["version_id"]) for d in done if UUID(d["org_id"]) not in FIXTURE_ORGS],
                          fetch="fetchval") if real_plan else 0
    check(int(vers) == len(real_plan), "each restatement left a closed system-axis version (id kept on the live row)",
          f"{vers}/{len(real_plan)}")
    # Pair each restated deal with its OWN version by id (not by created_at —
    # every fixture deal is seeded in one transaction, so they all share it).
    status_moved = []
    for d in done:
        live = await platform(conn, "SELECT deal_status::text FROM deals WHERE id = $1 AND system_to IS NULL",
                              UUID(d["deal_id"]), fetch="fetchval")
        prior = await platform(conn, "SELECT deal_status::text FROM deals WHERE id = $1",
                               UUID(d["version_id"]), fetch="fetchval")
        if not (live == prior == d["deal_status"]):
            status_moved.append((d["name"], d["deal_status"], prior, live))
    check(bool(done) and not status_moved, "the repair never changed a deal_status (each deal vs its own version)",
          f"{len(done)} restated; moved: {status_moved}")


async def y_actions(conn):
    section("[Y] MKT-03 / R2-MKT-05 — submit validation, archive confirmation, un-archive")
    code, body = await api(U_STAFF, "PUT", f"/api/v1/deals/{D_SUBMIT_MISSING}/status",
                           {"status": "submitted", "confirm": True})
    missing = [m.get("label") for m in ((body or {}).get("detail") or {}).get("missing_fields", [])] \
        if isinstance((body or {}).get("detail"), dict) else []
    check(code == 422 and "Target raise" in missing and "Target raise" in dumps(body),
          "Submit refuses a deal missing a required field, naming it", f"HTTP {code} {dumps(body)}")
    st = await scoped(conn, ORG_A, "SELECT deal_status::text FROM deals WHERE id = $1 AND system_to IS NULL",
                      D_SUBMIT_MISSING, fetch="fetchval")
    check(st == "draft", "…and the deal stays a draft (re-read)", f"{st}")
    code, body = await api(U_STAFF, "PUT", f"/api/v1/deals/{D_DRAFT}/status", {"status": "submitted"})
    check(code == 428, "Submit without confirm=true is refused (428)", f"HTTP {code}")
    before = await scoped(conn, ORG_A, "SELECT deal_status::text FROM deals WHERE id = $1 AND system_to IS NULL",
                          D_ARCHIVE_ME, fetch="fetchval")
    code, body = await api(U_STAFF, "PUT", f"/api/v1/deals/{D_ARCHIVE_ME}/status", {"status": "archived"})
    after = await scoped(conn, ORG_A, "SELECT deal_status::text FROM deals WHERE id = $1 AND system_to IS NULL",
                         D_ARCHIVE_ME, fetch="fetchval")
    check(code == 428 and before == after == "under_review",
          "Archive without confirmation is refused and changes nothing", f"HTTP {code}; {before}->{after}")
    code, body = await api(U_STAFF, "PUT", f"/api/v1/deals/{D_ARCHIVE_ME}/status",
                           {"status": "archived", "confirm": True})
    after = await scoped(conn, ORG_A, "SELECT deal_status::text FROM deals WHERE id = $1 AND system_to IS NULL",
                         D_ARCHIVE_ME, fetch="fetchval")
    check(code == 200 and after == "archived", "confirmed Archive archives (re-read)", f"HTTP {code} {after}")
    code, lc = await api(U_STAFF, "GET", f"/api/v1/deals/{D_ARCHIVE_ME}/lifecycle")
    check(code == 200 and lc.get("unarchive_to") == "under_review" and lc.get("unarchive_to_label") == "Under Review",
          "the lifecycle envelope offers Un-archive back to the prior stage", dumps(lc))
    code_m, _ = await api(U_MEMBER, "POST", f"/api/v1/deals/{D_ARCHIVE_ME}/unarchive")
    check(code_m == 403, "a member is refused Un-archive (manage_deals, same as Archive)", f"HTTP {code_m}")
    code, body = await api(U_STAFF, "POST", f"/api/v1/deals/{D_ARCHIVE_ME}/unarchive")
    after = await scoped(conn, ORG_A, "SELECT deal_status::text, deal_stage FROM deals WHERE id = $1 "
                                      "AND system_to IS NULL", D_ARCHIVE_ME, fetch="fetchrow")
    check(code == 200 and after["deal_status"] == "under_review" and after["deal_stage"] == "deal_stage_3",
          "Un-archive restores the prior stage, mirror included (re-read)", f"HTTP {code} {dict(after)}")
    await asyncio.sleep(0.5)  # write_audit_log is a separate, best-effort connection
    aud = await platform(conn, "SELECT payload FROM audit_log WHERE resource_id = $1 AND action = 'status_change' "
                               "ORDER BY created_at DESC LIMIT 1", D_ARCHIVE_ME, fetch="fetchval")
    aud = json.loads(aud) if isinstance(aud, str) else (aud or {})
    check(aud.get("unarchive") is True and aud.get("from") == "archived" and aud.get("deal_status") == "under_review",
          "the un-archive is audited (status_change, unarchive=true, from archived)", dumps(aud))
    hist = [r["s"] for r in await scoped(
        conn, ORG_A, "SELECT deal_status::text AS s FROM deals WHERE org_id = $1 AND system_to IS NOT NULL "
                     "AND id <> $2 AND created_at = (SELECT created_at FROM deals WHERE id = $2) "
                     "ORDER BY system_to DESC LIMIT 2", ORG_A, D_ARCHIVE_ME)]
    check(hist == ["archived", "under_review"],
          "archive and un-archive each left a bi-temporal version (newest: archived, then under_review)", f"{hist}")
    code, _ = await api(U_STAFF, "PUT", f"/api/v1/deals/{D_ACTIVE}", {"deal_status": "draft"})
    check(code == 400, "the generic edit can no longer jump the lifecycle (deal_status refused)", f"HTTP {code}")

    section("[Y] TEST-Submit Test — restored through the Un-archive endpoint")
    before = await platform(conn, "SELECT deal_status::text FROM deals WHERE id = $1 AND system_to IS NULL",
                            TEST_SUBMIT, fetch="fetchval")
    print(f"[INFO] TEST-Submit Test before: {before}")
    if before == "archived":
        code, body = await api(U_RESTORER, "POST", f"/api/v1/deals/{TEST_SUBMIT}/unarchive")
        REPAIR_DELTAS["public.deals"] = REPAIR_DELTAS.get("public.deals", 0) + 1
        REPAIR_DELTAS["public.audit_log"] = REPAIR_DELTAS.get("public.audit_log", 0) + 1
    else:
        code, body = 200, {"note": "already restored by an earlier run"}
    after = await platform(conn, "SELECT deal_status::text FROM deals WHERE id = $1 AND system_to IS NULL",
                           TEST_SUBMIT, fetch="fetchval")
    print(f"[INFO] TEST-Submit Test after: {after}")
    check(code == 200 and after == "submitted", "TEST-Submit Test is back at 'submitted' (independent re-read)",
          f"HTTP {code} {dumps(body)}")


async def y_member_votes(conn):
    section("[Y] MKT-04 / VOTE-01 — member names; vote withdraw by its owner only")
    old = git_show(BEFORE_REF, "apps/web/components/marketplace/MemberInvestmentTracker.jsx")
    check("String(inv.user_id).slice(0, 8)" in old, "BEFORE: the Pipeline tab printed a truncated user id")
    code, rows = await api(U_STAFF, "GET", f"/api/v1/deals/{D_ACTIVE}/member-investments")
    row = next((r for r in rows if r.get("user_id") == str(U_MEMBER)), {}) if isinstance(rows, list) else {}
    check(code == 200 and row.get("member_name") == "BUGFIX1 Member", "AFTER: the member's name is returned",
          dumps(row))
    code_m, _ = await api(U_MEMBER, "GET", f"/api/v1/deals/{D_ACTIVE}/member-investments")
    check(code_m == 403, "a member is still refused the member-investment list", f"HTTP {code_m}")
    code, body = await api(U_MEMBER, "DELETE", f"/api/v1/deals/{D_ACTIVE}/vote")
    left = {r["user_id"] for r in await scoped(conn, ORG_A, "SELECT user_id FROM deal_votes WHERE deal_id = $1",
                                               D_ACTIVE)}
    check(code == 200 and U_MEMBER not in left and U_MEMBER2 in left,
          "the owner withdraws their vote; the other member's vote stays (re-read)", f"HTTP {code} {left}")
    code, _ = await api(U_MEMBER, "DELETE", f"/api/v1/deals/{D_ACTIVE}/vote")
    check(code == 404, "withdrawing again: 404 (no vote of theirs) — never someone else's", f"HTTP {code}")
    left2 = {r["user_id"] for r in await scoped(conn, ORG_A, "SELECT user_id FROM deal_votes WHERE deal_id = $1",
                                                D_ACTIVE)}
    check(left2 == {U_MEMBER2}, "the other member's vote is untouched", f"{left2}")
    code, _ = await api(U_OTHER, "DELETE", f"/api/v1/deals/{D_ACTIVE}/vote")
    check(code == 404, "another org's member cannot reach the deal (404)", f"HTTP {code}")


async def y_search(conn):
    from services import document_linkage as dl

    section("[Y] R2-DOC-03 — search covers deal / entity / SPV attachments under each record's rule")
    async with conn.transaction():
        await conn.execute("SELECT set_config('app.current_org_id', $1, true), "
                           "set_config('app.is_super_admin', 'false', true)", str(ORG_A))
        old = await dl._search_rows(conn, str(ORG_A), TOKEN, 50, scope=None)
    check(len(old) == 0, "BEFORE: the vault-only query finds none of the six org-A attachments", f"{len(old)}")

    async def ids(uid, q):
        code, body = await api(uid, "GET", f"/api/v1/document-search?q={q}")
        res = (body or {}).get("results") or []
        return code, {r["document_id"]: r for r in res}

    code, m = await ids(U_MEMBER, TOKEN)
    check(code == 200 and {str(DD_ACTIVE), str(ED_MINE), str(SD_S1)} <= set(m),
          "member finds the deal, entity and SPV attachment by name", f"{sorted(m)}")
    check(m.get(str(DD_ACTIVE), {}).get("source_label") == "Deal" and m.get(str(ED_MINE), {}).get("source_label")
          == "Entity" and m.get(str(SD_S1), {}).get("source_label") == "SPV",
          "each labelled with its source (Deal / Entity / SPV)")
    check(m.get(str(DD_ACTIVE), {}).get("href", "").startswith(f"/marketplace/{D_ACTIVE}")
          and m.get(str(ED_MINE), {}).get("href", "").startswith(f"/crm/{E_MINE}")
          and m.get(str(SD_S1), {}).get("href", "").startswith(f"/spvs/{S1}"),
          "each links to the record it lives on")
    hidden = {str(DD_DRAFT), str(ED_RESTR), str(ED_OTHER), str(SD_RESTR), str(DD_B)}
    check(not (hidden & set(m)), "member never sees a draft deal's, another entity's, a restricted entity's, "
                                 "a restricted SPV's or another org's attachment", f"{hidden & set(m)}")
    expected_member = {r["id"] for r in await scoped(conn, ORG_A, """
        SELECT dd.id FROM deal_documents dd JOIN deals d ON d.id = dd.deal_id
        WHERE dd.file_name LIKE $1 AND d.deal_status::text IN ('active','under_review')
          AND dd.status = 'approved' AND dd.visible_to_members AND d.system_to IS NULL
        UNION SELECT id FROM entity_documents WHERE title LIKE $1 AND entity_id = $2
        UNION SELECT sd.id FROM spv_documents sd JOIN spvs s ON s.id = sd.spv_id
        WHERE sd.title LIKE $1 AND s.spv_status IN ('open','closing') AND s.vehicle_entity_id = $2""",
        f"{TOKEN}%", E_MINE)}
    check(len(expected_member) == 3 and {UUID(k) for k in m} == expected_member,
          "member's set equals the same predicates run directly in SQL", f"{len(expected_member)}")
    code, mt = await ids(U_MEMBER, TEXT_TOKEN)
    check(code == 200 and set(mt) == {str(DD_ACTIVE)} and "extracted_text" in mt[str(DD_ACTIVE)]["matched_on"],
          "member finds the deal attachment by its extracted text", dumps(mt))
    find("entity_documents and spv_documents have no extracted-text column and no extraction runs on them",
         "they are searchable by name and category only; text search covers deal_documents.extracted_data")
    code, s = await ids(U_STAFF, TOKEN)
    check(code == 200 and {str(DD_ACTIVE), str(DD_DRAFT), str(ED_MINE), str(ED_OTHER), str(SD_S1)} <= set(s)
          and str(ED_RESTR) not in s and str(DD_B) not in s,
          "staff sees every assigned record's attachment incl. the draft deal's; not the restricted one",
          f"{sorted(s)}")
    check(set(m) < set(s), "identical request: member's set is a strict subset of staff's")
    code, o = await ids(U_OTHER, TOKEN)
    check(code == 200 and str(DD_B) in o and not (set(o) & {str(x) for x in (DD_ACTIVE, ED_MINE, SD_S1)}),
          "the other org sees only its own attachment", f"{sorted(o)}")

    # semantic search: items without an embedding are named, not silently dropped
    from services import document_embedding

    async def fake_semantic(*a, **k):
        return []

    document_embedding.semantic_search = fake_semantic
    code, sem = await api(U_MEMBER, "GET", f"/api/v1/semantic-search?q={TOKEN}")
    ni = {r["document_id"] for r in (sem or {}).get("not_indexed", [])}
    check(code == 200 and {str(DD_ACTIVE), str(ED_MINE), str(SD_S1)} <= ni
          and "not yet indexed" in (sem or {}).get("not_indexed_note", "").lower() and not (ni & hidden),
          "semantic search lists the caller's unembedded matches as 'not yet indexed for meaning search'",
          dumps(sem))


async def y_pages(conn):
    section("[Y] Dead links — hidden from every menu, 'Coming soon' at their URLs; /fee-schedules resolved")
    code, out, err = run_cmd(["node", "--test", "tests/bugfix1/"], WEB_DIR, timeout=300)
    check(code == 0, "node --test tests/bugfix1 passes", (out + err)[-300:] if code else "")
    for name in ("DEAD-LINKS: comingSoon items are absent from every persona's menu; the page says Coming soon",):
        check(re.search(rf"^ok \d+ - {re.escape(name)}", out, re.M) is not None, f"node test: {name}")
    for href in ("community", "insurance", "portfolio-reporting"):
        page = WEB_DIR / "app" / href / "page.js"
        check(page.exists() and f'renderComingSoon("/{href}")' in page.read_text(encoding="utf-8"),
              f"/{href} renders the Coming soon page (was 404)")
    fee = WEB_DIR / "app" / "fee-schedules" / "page.js"
    check(fee.exists() and 'redirect("/fee-schedules/chat")' in fee.read_text(encoding="utf-8"),
          "/fee-schedules routes to its only page, the fee schedule assistant")
    menu_src = (WEB_DIR / "lib" / "menuVisibility.mjs").read_text(encoding="utf-8")
    flagged = re.findall(r"^\s*\{ \.\.\..*\bcomingSoon: true \},", menu_src, re.M)   # entries, not the doc comment
    check(len(flagged) == 3 and "export const COMING_SOON" in menu_src,
          "the feature flag lives in the ONE menu definition")


async def y_crm(conn):
    section("[Y] CRM-02 — the Ownership tab shows what the Overview shows")
    old = await scoped(conn, ORG_A, "SELECT count(*) FROM entity_relationships WHERE (from_entity_id = $1 OR "
                                    "to_entity_id = $1) AND relationship_type = 'ownership' AND valid_to IS NULL",
                       E_LLC, fetch="fetchval")
    check(int(old) == 0, "BEFORE: the tab's table (entity_relationships) holds nothing for the fixture")
    code_f, full = await api(U_STAFF, "GET", f"/api/v1/entities/{E_LLC}/full")
    code_o, own = await api(U_STAFF, "GET", f"/api/v1/entities/{E_LLC}/ownership")
    code_g, graph = await api(U_STAFF, "GET", f"/api/v1/entities/{E_LLC}/ownership-graph")
    ov = sorted((o["parent_id"], o["ownership_pct"]) for o in (full or {}).get("owners", []))
    tab = sorted((i["counterparty"]["id"], i["ownership_pct"]) for i in (own or {}).get("owned_by", []))
    gr = sorted((e["parent_id"], e["ownership_pct"]) for e in (graph or {}).get("edges", [])
                if e["child_id"] == str(E_LLC))
    check(code_f == code_o == code_g == 200 and ov == tab == gr == sorted([(str(E_MINE), 40.0), (str(E_TRUST), 60.0)]),
          "Overview, Ownership tab list and tree all show Mine 40% + Trust 60%", f"{ov} | {tab} | {gr}")
    check(all(i.get("editable") is False for i in (own or {}).get("owned_by", [])),
          "rows from the other table are shown read-only (no edit control the endpoint cannot honour)")

    section("[Y] CRM-04 — duplicate current tax IDs")
    code1, b1 = await api(U_ORGADMIN, "POST", f"/api/v1/entities/{E_LLC}/tax-ids",
                          {"tax_id_type": "ein", "tax_id_country": "US", "value": "12-3456789", "is_primary": True})
    code2, b2 = await api(U_ORGADMIN, "POST", f"/api/v1/entities/{E_LLC}/tax-ids",
                          {"tax_id_type": "ein", "tax_id_country": "US", "value": "12-3456789", "is_primary": True})
    n = await scoped(conn, ORG_A, "SELECT count(*) FROM entity_tax_ids WHERE entity_id = $1 AND valid_to IS NULL",
                     E_LLC, fetch="fetchval")
    check(code1 == 201 and code2 == 409 and int(n) == 1, "a second identical tax ID is refused (409); one row",
          f"{code1}/{code2}, rows {n}")
    import _bugfix1_repairs as R

    plan = await R.tax_dup_plan(conn)
    for p in plan:
        print(f"[INFO] real duplicate tax-ID row closed: {p['id']} entity {p['entity_id']} {p['type']}/{p['country']}")
    await R.apply_tax_dups(conn, plan)
    REPAIR_DELTAS["public.audit_log"] = REPAIR_DELTAS.get("public.audit_log", 0) + len(plan)
    left = await R.tax_dup_plan(conn)
    check(len(left) == 0, "no entity has two identical current tax-ID rows (re-read)", f"{len(plan)} closed")
    hg = await platform(conn, "SELECT count(*) FILTER (WHERE valid_to IS NULL) cur, count(*) tot FROM entity_tax_ids "
                              "WHERE entity_id = '10000000-0000-0000-0000-000000000003'", fetch="fetchrow")
    check(hg is not None and hg["cur"] == 1 and hg["tot"] >= 2,
          "James Hargrove: one current SSN row; the duplicate is closed, not deleted", dumps(dict(hg or {})))


async def y_portfolio(conn):
    section("[Y] PORT-01 / PORT-02")
    code, pos = await api(U_OTHER, "GET", "/api/v1/portfolio/positions")
    inv = await scoped(conn, ORG_B, "SELECT count(*) FROM member_investments WHERE user_id = $1", U_OTHER,
                       fetch="fetchval")
    check(code == 200 and (pos or {}).get("total") == 0 and int(inv) == 1,
          "BEFORE condition reproduced: holdings empty while the member has an investment")
    hint = (pos or {}).get("empty_hint") or {}
    check("imported from the custodian" in hint.get("message", "") and hint.get("href") == "/portfolio",
          "AFTER: the empty grid explains why and links to the investments", dumps(hint))
    code, sec = await api(U_OTHER, "GET", "/api/v1/portfolio/securities")
    check(code == 200 and bool((sec or {}).get("empty_hint")), "the Securities grid carries the same hint")
    code, posa = await api(U_STAFF, "GET", "/api/v1/portfolio/positions")
    check(code == 200 and (posa or {}).get("total", 0) > 0 and (posa or {}).get("empty_hint") is None,
          "no hint when there are holdings")
    code_m, cm = await api(U_MEMBER, "GET", "/api/v1/portfolio/commitments")
    code_s, cs = await api(U_STAFF, "GET", "/api/v1/portfolio/commitments")
    code_o, co = await api(U_OTHER, "GET", "/api/v1/portfolio/commitments")
    code_n, _ = await api(U_NOROLE, "GET", "/api/v1/portfolio/commitments")
    mids = {r["id"] for r in (cm or {}).get("rows", [])}
    sids = {r["id"] for r in (cs or {}).get("rows", [])}
    check(code_m == 200 and mids == {str(CM_MINE)}, "member: only the commitment on their own entity", f"{mids}")
    check(code_s == 200 and {str(CM_MINE), str(CM_OTHER)} <= sids, "staff: both assigned entities' commitments")
    check(code_o == 200 and not ({str(CM_MINE), str(CM_OTHER)} & {r["id"] for r in (co or {}).get("rows", [])}),
          "other org: none of these")
    check(code_n == 403, "a role-less user is refused (view_portfolio)", f"HTTP {code_n}")
    r = next(iter((cm or {}).get("rows", [])), {})
    check(r.get("fund") == "BF1 Fund III" and r.get("name") == "BF1 Mine" and r.get("remaining") == "750000"
          and r.get("href") == f"/portfolio/commitments/{CM_MINE}", "row: name, fund, commitment, called, remaining",
          dumps(r))


async def y_edgar(conn):
    from services import edgar_bulk as eb
    from services import edgar_fetch, edgar_pipeline, storage

    section("[Y] EDGAR — the peek decides BEFORE the full download (mocked SEC and R2)")
    PREL = (b"<html><body><p>PRELIMINARY PRICING SUPPLEMENT Subject to Completion</p>"
            + b"<p>filler</p>" * 8000 + b"</body></html>")
    FINAL = b"<html><body><p>PRICING SUPPLEMENT dated 2026</p>" + b"<p>terms</p>" * 3000 + b"</body></html>"
    UNREADABLE_PREL = (b"<html><body>" + b"<div></div>" * 3000
                       + b"<p>PRELIMINARY PRICING SUPPLEMENT Subject to Completion</p></body></html>")
    log: list[dict] = []
    docs = {}

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        rng = request.headers.get("range")
        log.append({"url": url, "range": rng})
        if url.endswith("/index.json"):
            return httpx.Response(200, json={"directory": {"item": [{"name": "doc.htm", "size": "9"}]}})
        if url.endswith("-index-headers.html"):
            return httpx.Response(200, text="FILE NUMBER: 333-000001")
        body, mode = docs[url.split("/")[-2]]
        if rng and mode == "honour":
            n = int(rng.split("-")[1]) + 1
            return httpx.Response(206, content=body[:n],
                                  headers={"Content-Range": f"bytes 0-{min(n, len(body)) - 1}/{len(body)}"})
        return httpx.Response(200, content=body)

    stored, skipped, uploads = [], [], []

    async def fake_store(conn_, meta, raw, **k):
        stored.append(meta.accession_number)
        return {"status": "ready_for_extraction", "document_kind": "pricing_supplement",
                "bytes_uploaded": len(raw), "reference_filing_id": None}

    async def fake_skip(conn_, run_id, row, p):
        skipped.append((row["accession_number"], p.kind, "peek"))

    async def fake_kind_skip(conn_, run_id, row, kind, reason, n):
        skipped.append((row["accession_number"], kind, "full"))

    async def fake_release(*a, **k):
        return 0

    orig = (edgar_pipeline.store_fetched, eb.record_peek_skip, eb.record_kind_skip, eb.release_claims)
    edgar_pipeline.store_fetched, eb.record_peek_skip, eb.record_kind_skip, eb.release_claims = (
        fake_store, fake_skip, fake_kind_skip, fake_release)
    storage.upload_bytes = lambda *a, **k: uploads.append(a)

    class FakeConn:
        def transaction(self):
            class T:
                async def __aenter__(s): return s
                async def __aexit__(s, *a): return False
            return T()

        async def execute(self, *a): return "SELECT 1"
        async def fetch(self, *a): return []

    try:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            runner = eb.Runner(None, eb.RunOptions(kinds=["final"]), client=client, backoff_base=0.001)
            runner.scope = SimpleNamespace(source=SimpleNamespace(classify=edgar_pipeline.classify_document))
            runner.wanted, runner.bucket, runner.policy_rules = {"pricing_supplement"}, "verify-bucket", {}
            runner.want_supplements = False
            cases = {"0009900001-26-000001": (PREL, "honour"), "0009900001-26-000002": (PREL, "ignore"),
                     "0009900001-26-000003": (FINAL, "honour"), "0009900001-26-000004": (FINAL, "ignore"),
                     "0009900001-26-000005": (UNREADABLE_PREL, "ignore")}
            out = {}
            for acc, (body, mode) in cases.items():
                docs[acc.replace("-", "")] = (body, mode)
                row = {"accession_number": acc, "form_type": "424B2", "filing_date": date(2026, 1, 2),
                       "submission_path": f"edgar/data/99001/{acc}.txt", "primary_issuer_cik": "99001"}
                n0 = len(log)
                out[acc] = (await runner._fetch_and_store(FakeConn(), row, "Verify Bank"), log[n0:])
    finally:
        edgar_pipeline.store_fetched, eb.record_peek_skip, eb.record_kind_skip, eb.release_claims = orig

    def doc_gets(reqs, full_only=False):
        return sum(1 for e in reqs if e["url"].endswith("/doc.htm") and (not full_only or not e["range"]))

    for acc, label in (("0009900001-26-000001", "206"), ("0009900001-26-000002", "200 (Range ignored)")):
        res, reqs = out[acc]
        check(res.get("peek_skipped") is True and res.get("document_kind") == "preliminary_pricing_supplement"
              and doc_gets(reqs) == 1 and acc not in stored and (acc, "preliminary_pricing_supplement", "peek") in skipped,
              f"a preliminary ({label}) is recorded with its kind, ONE peek request, no full GET, no store",
              f"{res}; doc GETs {doc_gets(reqs)}")
    check(runner.stats.bytes_downloaded < len(PREL) * 2 + len(FINAL) * 3,
          "a Range-ignored preliminary costs the peek's bytes, not the document", f"{runner.stats.bytes_downloaded}")
    for acc, label in (("0009900001-26-000003", "206"), ("0009900001-26-000004", "200")):
        res, reqs = out[acc]
        check(acc in stored and not res.get("peek_skipped"), f"a final ({label}) continues to the full fetch and store")
    res, reqs = out["0009900001-26-000004"]
    check(doc_gets(reqs) == 1, "a 200 final is read to the end in place (no second document GET)",
          f"{doc_gets(reqs)}")
    res, reqs = out["0009900001-26-000005"]
    check(res.get("peek_skipped") is True and "0009900001-26-000005" not in stored
          and ("0009900001-26-000005", "preliminary_pricing_supplement", "full") in skipped,
          "a preliminary whose cover the peek cannot read is caught on its full text and NOT stored")
    check(not uploads and not R2_CALLS, "nothing was written to R2")
    check(all(e["url"].startswith("https://www.sec.gov/") for e in log), "only the mocked SEC was addressed",
          f"{log[:1]}")

    section("[Y] EDGAR — preliminary R2 cleanup: dry run reports, changes nothing")
    before = await platform(conn, "SELECT count(*) FROM portfolio.reference_filings WHERE r2_key IS NOT NULL",
                            fetch="fetchval")
    plan = await eb.plan_preliminary_cleanup(conn)
    after = await platform(conn, "SELECT count(*) FROM portfolio.reference_filings WHERE r2_key IS NOT NULL",
                           fetch="fetchval")
    check(isinstance(plan.get("count"), int) and isinstance(plan.get("bytes"), int) and before == after,
          "dry run: count and bytes reported, reference_filings unchanged",
          f"{plan.get('count')} filings, {plan.get('objects')} objects, {plan.get('bytes')} bytes")
    deleted, executed = [], []

    class RecConn(FakeConn):
        async def execute(self, *a):
            executed.append(a)
            return "UPDATE 1"

    fake_plan = {"rows": [{"id": UUID(int=1), "accession_number": "x", "r2_key": "k1", "text_r2_key": "k2"}]}
    res = await eb.apply_preliminary_cleanup(RecConn(), fake_plan, bucket="verify-bucket", deleter=deleted.append)
    check(deleted == ["k1", "k2"] and res["cleared"] == 1
          and any("r2_key = NULL" in str(a[0]) for a in executed),
          "--apply (mocked deleter): both objects deleted, then both keys cleared")

    section("[Y] EDGAR — Institutions table header; laptop worker default")
    from services import edgar_bulk_queue as q

    check("apps/api/venv/bin/python -u apps/api/scripts/run_bulk_fetch.py" in q.LAPTOP_COMMAND,
          "the worker default command uses apps/api/venv/bin/python", q.LAPTOP_COMMAND)
    code, out_t, err = run_cmd(["node", "--test", "tests/bugfix1/"], WEB_DIR, timeout=300)
    check(re.search(r"^ok \d+ - EDGAR: the Institutions table has a header row", out_t, re.M) is not None,
          "node test: the Institutions table renders a header row (Institution, Entities, years, Total)")


async def y_polish(conn):
    section("[Y] DASH-02 / R2-ASSIST-03 / FIRM-01")
    code, brief = await api(U_STAFF, "GET", "/api/v1/dashboard/brief")
    items = next((b["data"]["items"] for b in (brief or {}).get("blocks", []) if b["key"] == "needs_attention"), [])
    hrefs = {i["title"]: i.get("href") for i in items}
    check(code == 200 and hrefs.get("Complete profile: BF1 Stub") == "/crm?q=BF1%20Stub"
          and hrefs.get("Complete profile: BF1 Mine") == f"/crm/{E_MINE}",
          "each to-do carries the link where it is resolved", dumps(hrefs))
    code, todos = await api(U_STAFF, "GET", "/api/v1/dashboard/todos")
    check(code == 200 and all(t.get("href") for t in (todos or {}).get("actual", [])),
          "GET /dashboard/todos publishes the same href")

    from routers import assistant
    from services.assistant_transcript import user_visible_text

    stored = [
        {"role": "user", "content": "how many deals?", "_display": None},
        {"role": "assistant", "content": [{"type": "text", "text": "Let me look that up."},
                                          {"type": "tool_use", "id": "t", "name": "x", "input": {}}],
         "_display": {"text": "Let me look that up.", "disclosures": [], "render": None}},
        {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t", "content": "{}"}], "_display": None},
        {"role": "assistant", "content": [{"type": "text", "text": "*Checking the deals tool*\nThere are 4 deals."}],
         "_display": {"text": "*Checking the deals tool*\nThere are 4 deals.", "disclosures": ["Counted deals"],
                      "render": None}},
    ]
    view = assistant._format_conversation({"id": UUID(int=2), "messages": stored, "context_ref": None})
    texts = [m["text"] for m in view["messages"] if m["role"] == "assistant"]
    check(texts == ["There are 4 deals."] and all(not m.get("disclosures") for m in view["messages"]
                                                  if m["role"] == "assistant"),
          "a stored transcript shows no tool narration, no italic system line, no disclosures", f"{texts}")
    check(user_visible_text("_Using the deals tool_\nDone.") == "Done.", "narration lines are stripped server-side")

    async def fake_tools(**kw):
        n = len([m for m in kw["messages"] if m["role"] == "assistant"])
        if n == 0:
            return {"stop_reason": "tool_use", "content": [{"type": "text", "text": "Let me check your deals."},
                                                           {"type": "tool_use", "id": "a", "name": "noop", "input": {}}]}
        return {"stop_reason": "end_turn", "content": [{"type": "text", "text": "You have 2 deals."}]}

    async def fake_brand(*a, **k):
        return "Verify"

    assistant.call_claude_with_tools = fake_tools
    assistant.get_brand_name = fake_brand
    res = await assistant._run_loop([{"role": "user", "content": "q"}], [], {}, None, str(U_MEMBER), str(ORG_A), False)
    internal = [m for m in res["new_messages"] if (m.get("_display") or {}).get("internal")]
    check(res["message"] == "You have 2 deals." and internal and internal[0]["_display"]["narration"]
          == "Let me check your deals.", "the reply is the final answer; the narration is kept, internal",
          dumps(res["message"]))

    page = (WEB_DIR / "app" / "firm-search" / "page.js").read_text(encoding="utf-8")
    old = git_show(BEFORE_REF, "apps/web/app/firm-search/page.js")
    page_code = re.sub(r"//.*", "", page)   # the comments name the old hard-code; the code must not
    check('title: "Find your firm — Hollisworks"' in old and "Hollisworks" not in page_code
          and "firmSearchBrand" in page_code, "BEFORE: /firm-search hard-coded Hollisworks; AFTER: the host's brand")
    brand_src = (WEB_DIR / "lib" / "firmSearchBrand.js").read_text(encoding="utf-8")
    check("loadTheme()" in brand_src and "tenant.marketing" in brand_src,
          "a tenant host (the 2nd Act site) reads its own org branding; only the marketing apex uses the platform's")
    from services.org_settings import get_setting
    from services.tenant import resolve_tenant

    async with conn.transaction():
        await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")
        t = await resolve_tenant(conn, "2ndactcapital.com")
        name = await get_setting(conn, DEFAULT_ORG, "brand.name")
    t = t if isinstance(t, dict) else dict(t or {})
    check(t.get("marketing") is False and str(t.get("org_id")) == str(DEFAULT_ORG) and bool(name)
          and "Hollisworks" not in str(name),
          "on the 2nd Act host the tenant resolves to 2nd Act, whose brand.name is used", f"{name}")


async def y_gates(conn):
    section("[Y] Every changed endpoint keeps its gate — identical request, three callers")
    before = json.loads(git_show(BEFORE_REF, "docs/AUTHZ1_ROUTES.json") or "{}")
    now = json.loads((REPO / "docs" / "AUTHZ1_ROUTES.json").read_text(encoding="utf-8"))

    def gates(d):
        rows = d.get("routes", d) if isinstance(d, dict) else d
        return {(tuple(r.get("methods") or []), r["path"]): r.get("after") for r in rows} if isinstance(rows, list) else {}

    gb, gn = gates(before), gates(now)
    changed = [k for k in gb if k in gn and gb[k] != gn[k]]
    check(len(gb) > 0 and not changed, "no existing route's gate changed (AUTHZ1_ROUTES.json before vs now)",
          f"{changed[:5]}")
    new = sorted(set(gn) - set(gb))
    # DEALROOM1 (a later sprint) added the /deal-room/* routes on purpose; they
    # are proven by verify_dealroom1. This check keeps proving bugfix1's own four.
    later = [k for k in new if k[1].startswith("/api/v1/deal-room/") or k[1] == "/api/v1/deal-room"]
    new = [k for k in new if k not in later]
    if later:
        print(f"[INFO] {len(later)} route(s) added by sprint dealroom1 — excluded from bugfix1's count")
    check(all(gn[k] != "none" for k in new if k[0] != ("GET",)) and len(new) == 4,
          "the four new routes each carry a gate", f"{[(k, gn[k]) for k in new]}")
    code, out, err = run_cmd([sys.executable, str(HERE / "authz1_routes.py"), "--check"], REPO, timeout=300)
    check(code == 0 and "CURRENT" in out, "authz1_routes.py --check: the route doc is current", (out + err)[-200:])
    matrix = [
        ("GET", f"/api/v1/spvs/{S1}/captable", {U_ORGADMIN: 200, U_MEMBER: 403, U_SUPER: 200}),
        ("GET", "/api/v1/deals/stage-summary", {U_ORGADMIN: 200, U_MEMBER: 200, U_SUPER: 200}),
        ("GET", f"/api/v1/deals/{D_ACTIVE}/member-investments", {U_ORGADMIN: 200, U_MEMBER: 403, U_SUPER: 200}),
        ("GET", f"/api/v1/deals/{D_DRAFT}/lifecycle", {U_ORGADMIN: 200, U_MEMBER: 404, U_SUPER: 200}),
        ("POST", f"/api/v1/deals/{D_ACTIVE}/unarchive", {U_ORGADMIN: 400, U_MEMBER: 403, U_SUPER: 400}),
        ("GET", "/api/v1/portfolio/commitments", {U_ORGADMIN: 200, U_MEMBER: 200, U_SUPER: 200}),
        ("GET", f"/api/v1/document-search?q={TOKEN}", {U_ORGADMIN: 200, U_MEMBER: 200, U_SUPER: 200}),
        ("GET", "/api/v1/dashboard/todos", {U_ORGADMIN: 200, U_MEMBER: 200, U_SUPER: 200}),
    ]
    for method, path, expect in matrix:
        got = {uid: (await api(uid, method, path))[0] for uid in expect}
        check(got == expect, f"{method} {path.split('?')[0]}: org admin / member / super admin as gated",
              f"{ {USERS[k][2]: v for k, v in got.items()} }")


async def y_front_end(conn):
    section("[Y] Front end — node tests; npm run build")
    code, out, err = run_cmd(["node", "--test", "tests/"], WEB_DIR, timeout=600)
    m = re.search(r"^# fail (\d+)", out, re.M)
    check(code == 0 and m is not None and m.group(1) == "0", "node --test tests/ passes (0 fail)",
          (out + err)[-300:] if code else "")
    code, out, err = run_cmd(["npm", "run", "build"], WEB_DIR, timeout=1200)
    check(code == 0, "npm run build exits 0", (out + err)[-400:] if code else "")


def regression():
    section("[Y] Regression — flat subprocesses AFTER teardown, VERIFY_NESTED=1")
    env = os.environ | {"VERIFY_NESTED": "1"}
    for script in ("verify_authz1.py", "verify_navregroup.py", "verify_bulkfetchui2.py"):
        code, out, err = run_cmd([sys.executable, str(HERE / script)], REPO, timeout=1200, env=env)
        m = re.search(r"TOTAL: (\d+) PASS, (\d+) FAIL", out)
        fails = [ln for ln in out.splitlines() if ln.startswith("[FAIL]")]
        check(m is not None and m.group(2) == "0" and code == 0, f"{script}: 0 FAIL",
              f"{m.group(0) if m else 'no TOTAL'}; {fails[:3]} {err[-200:] if not m else ''}")


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
    role = await conn.fetchrow("SELECT current_user AS u, rolbypassrls FROM pg_roles WHERE rolname = current_user")
    print(f"[INFO] connected as {role['u']} (rolbypassrls={role['rolbypassrls']})")
    install_stubs()
    pre = gold = None
    try:
        await run("pre-teardown", teardown, conn)
        pre = await counts(conn)
        gold = await gold_fingerprint(conn)
        await run("seed", seed, conn)
        await run("task 1", y_task1, conn)
        await run("spv", y_spv, conn)
        await run("lifecycle", y_lifecycle, conn)
        await run("actions", y_actions, conn)
        await run("member + votes", y_member_votes, conn)
        await run("search", y_search, conn)
        await run("pages", y_pages, conn)
        await run("crm", y_crm, conn)
        await run("portfolio", y_portfolio, conn)
        await run("edgar", y_edgar, conn)
        await run("polish", y_polish, conn)
        await run("gates", y_gates, conn)
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
                    want = pre[t] + REPAIR_DELTAS.get(t, 0)
                    check(post[t] == want, f"{t} at its pre-test count"
                          + (f" + {REPAIR_DELTAS[t]} reported repair row(s)" if REPAIR_DELTAS.get(t) else ""),
                          f"{pre[t]} → {post[t]}")
            if gold is not None:
                check(await gold_fingerprint(conn) == gold, "real note_gold_values unchanged (count + checksum)",
                      f"{gold[0]} rows")
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
