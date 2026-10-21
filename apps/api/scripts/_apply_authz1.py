"""AUTHZ1 Task 3 — seed roles so nothing legitimate breaks, then report.

Idempotent and ADDITIVE ONLY: it ensures every real org has the baseline
roles of ``_authz1_common.BASELINE_ROLES`` (+ org_admin via
``services.rbac.ensure_org_admin_role``), adds missing permissions to them
(never removes one), and grants every real, active user the role matching
their ``users.role``. It never revokes, demotes, deletes or touches a fixture.

Writes docs/AUTHZ1_REAL_USERS.json: per real user, effective permissions
before (the pre-AUTHZ1 default-allow rule) and after (services.authz).

    python3 apps/api/scripts/_apply_authz1.py            # apply + report
    python3 apps/api/scripts/_apply_authz1.py --report   # report only

Optional, operator-only (NOT run by the sprint): ``--operator-link-google``
links Joe's Google login (google-oauth2|114785352792351858712) to the
jlarizza@gmail.com user and retires the placeholder row. Run it only after
confirming in the 2nd Act Auth0 dashboard that this identity's email is
jlarizza@gmail.com and verified — the runtime path does the same thing by
itself, from /userinfo, the next time that login signs in.
"""

from __future__ import annotations

import asyncio
import json
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from _db_connect import admin_dsn, connect  # noqa: E402

import _authz1_common as C  # noqa: E402
from services import rbac  # noqa: E402

REPORT = HERE.parents[2] / "docs" / "AUTHZ1_REAL_USERS.json"
GOOGLE_SUB = "google-oauth2|114785352792351858712"
GOOGLE_TARGET_EMAIL = "jlarizza@gmail.com"
ISSUER_2NDACT = "https://dev-smmrfubsfscif3t1.us.auth0.com/"


async def _platform(conn):
    await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")


async def seed(conn) -> list[str]:
    log = []
    orgs = [r["id"] for r in await conn.fetch(
        "SELECT id FROM organizations WHERE id::text NOT LIKE $1 ORDER BY id",
        C.FIXTURE_ORG_PREFIX + "%")]
    perm_ids = {r["name"]: r["id"] for r in await conn.fetch("SELECT id, name FROM permissions")}
    for org in orgs:
        await rbac.ensure_org_admin_role(conn, org)
        for name, (desc, perms) in C.BASELINE_ROLES.items():
            missing = sorted(set(perms) - set(perm_ids))
            if missing:
                raise SystemExit(f"permissions missing from the catalog: {missing}")
            role_id = await rbac.ensure_role(conn, org, name, desc)
            for p in sorted(perms):
                status = await conn.execute(
                    "INSERT INTO role_permissions (role_id, permission_id) VALUES ($1, $2) "
                    "ON CONFLICT DO NOTHING", role_id, perm_ids[p])
                if status.endswith(" 1"):
                    log.append(f"org {org}: role {name} += {p}")
    for u in await C.real_users(conn):
        if u["status"] != "active" or not u["is_active"]:
            continue
        target = C.ACCOUNT_ROLE_TO_ROLE.get(u["role"])
        if target is None:
            log.append(f"user {u['email']}: users.role {u['role']!r} has no mapped role — skipped")
            continue
        if target == "org_admin":
            if not await rbac.has_org_admin_grant(conn, u["id"], u["org_id"]):
                await rbac.grant_org_admin(conn, u["id"], u["org_id"])
                log.append(f"user {u['email']}: granted org_admin")
            continue
        role_id = await conn.fetchval(
            "SELECT id FROM roles WHERE org_id = $1 AND name = $2", u["org_id"], target)
        status = await conn.execute(
            "INSERT INTO user_roles (user_id, role_id) VALUES ($1, $2) ON CONFLICT DO NOTHING",
            u["id"], role_id)
        if status.endswith(" 1"):
            log.append(f"user {u['email']}: granted {target}")
    return log


async def operator_link_google(conn) -> str:
    row = await conn.fetchrow("SELECT id, org_id, status FROM users WHERE auth0_sub = $1", GOOGLE_SUB)
    target = await conn.fetchrow(
        "SELECT id, org_id FROM users WHERE lower(email) = lower($1) AND status = 'active'",
        GOOGLE_TARGET_EMAIL)
    if row is None or target is None:
        return "google placeholder or target not found — nothing done"
    if str(row["org_id"]) != str(target["org_id"]):
        return "placeholder and target are in different orgs — refusing (no cross-org merge)"
    await conn.execute(
        "INSERT INTO user_identities (user_id, org_id, auth0_sub, issuer, email, linked_via) "
        "VALUES ($1, $2, $3, $4, $5, 'operator') ON CONFLICT (auth0_sub) DO NOTHING",
        target["id"], target["org_id"], GOOGLE_SUB, ISSUER_2NDACT, GOOGLE_TARGET_EMAIL)
    await conn.execute("UPDATE users SET status = 'retired', updated_at = now() WHERE id = $1", row["id"])
    return "linked and retired"


async def references_to(conn, user_id) -> dict[str, int]:
    """Every FK column pointing at users(id), with this user's row count."""
    fks = await conn.fetch(
        """
        SELECT n.nspname AS sch, c.relname AS tbl, a.attname AS col
        FROM pg_constraint k
        JOIN pg_class c ON c.oid = k.conrelid
        JOIN pg_namespace n ON n.oid = c.relnamespace
        JOIN pg_attribute a ON a.attrelid = k.conrelid AND a.attnum = ANY(k.conkey)
        WHERE k.contype::text = 'f' AND k.confrelid = 'public.users'::regclass
        ORDER BY 1, 2, 3
        """)
    out = {}
    for fk in fks:
        n = await conn.fetchval(
            f'SELECT count(*) FROM "{fk["sch"]}"."{fk["tbl"]}" WHERE "{fk["col"]}" = $1', user_id)
        if n:
            out[f'{fk["sch"]}.{fk["tbl"]}.{fk["col"]}'] = int(n)
    return out


async def main() -> int:
    dsn, how = await admin_dsn()
    if not dsn:
        print(f"[FATAL] no working DSN: {how}")
        return 2
    conn = await connect(dsn)
    try:
        async with conn.transaction():
            await _platform(conn)
            before = await C.effective_report(conn, legacy=True)
        if "--report" not in sys.argv:
            async with conn.transaction():
                await _platform(conn)
                for line in await seed(conn):
                    print("[SEED]", line)
            if "--operator-link-google" in sys.argv:
                async with conn.transaction():
                    await _platform(conn)
                    print("[LINK]", await operator_link_google(conn))
        async with conn.transaction():
            await _platform(conn)
            after = await C.effective_report(conn, legacy=False)
            placeholder = await conn.fetchval("SELECT id FROM users WHERE auth0_sub = $1", GOOGLE_SUB)
            refs = await references_to(conn, placeholder) if placeholder else {}
    finally:
        await conn.close()

    # The FIRST run's "before" is the real pre-seed state; a re-run would
    # compute "before" from already-seeded roles, so it keeps the original.
    if REPORT.exists():
        prior = json.loads(REPORT.read_text(encoding="utf-8")).get("users", {})
        for email, p in prior.items():
            if email in before:
                before[email]["effective"] = p["effective_before"]
                before[email]["roles"] = p["roles_before"]
    report = {}
    for email, b in before.items():
        a = after.get(email, {})
        lost = sorted(set(b["effective"]) - set(a.get("effective", [])))
        gained = sorted(set(a.get("effective", [])) - set(b["effective"]))
        report[email] = {
            "user_id": b["user_id"], "org_id": b["org_id"], "account_role": b["account_role"],
            "status": a.get("status"), "roles_before": b["roles"], "roles_after": a.get("roles"),
            "is_super_admin_after": a.get("is_super_admin"),
            "effective_before": b["effective"], "effective_after": a.get("effective"),
            "lost": lost, "gained": gained,
            "lost_only_via_default_allow": bool(lost) and not b["roles"] and b["account_role"] != "super_admin",
        }
    payload = {"users": report, "google_placeholder_references": refs,
               "note": ("Before = pre-AUTHZ1 rbac rule (super admin all; no roles → all). "
                        "Every token-claim gate (spv/marketplace/entity_graph/...) also admitted "
                        "every real caller before AUTHZ1, regardless of the above.")}
    REPORT.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    for email, r in report.items():
        print(f"[REPORT] {email}: roles {r['roles_before']} → {r['roles_after']}; "
              f"{len(r['effective_before'])} → {len(r['effective_after'])} permissions; lost {r['lost']}")
    print(f"[REPORT] google placeholder references: {refs}")
    print(f"[INFO] wrote {REPORT}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
