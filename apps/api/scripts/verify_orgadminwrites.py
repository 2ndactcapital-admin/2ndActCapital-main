"""verify_orgadminwrites.py — org_admin write-path closure, real proof.

Follow-up to orgadminrole.structural (35/35, merged): that sprint migrated
every EXISTING org_admin holder to a real RBAC grant and switched every
READER (alerts, admin pages) to resolve by permission. It deliberately did
NOT intercept FUTURE writes. This sprint closes that gap in BOTH directions:

  * services/invites.py `create_invite` — used to write users.role='org_admin'
    with no user_roles grant (Task 2 fix: `services.rbac.grant_org_admin`
    called in the same transaction as the insert).
  * routers/admin.py `assign_role` (PUT /admin/users/{id}/role) — used to
    write the user_roles grant but never synced users.role, in EITHER
    direction (promotion never set the string; demotion never cleared it) —
    a privilege-ratchet-shaped bug even though the actual permission WAS
    correctly revoked on demotion.
  * routers/admin.py `list_roles` / `assign_role`'s role lookup were both
    ORG-BLIND (`SELECT ... FROM roles` / `WHERE id = $1`, no org_id filter) —
    a real, live cross-org bug this sprint also closes, since it would have
    let a fixed lockstep write leak a foreign org's role name onto
    users.role.

This script does NOT redo Task 2/3's real work — Task 3's backfill already
ran for real via `_reconcile_orgadminwrites_drift.py` (0 drift found, live
data was already exactly in sync). It proves the fix end to end, through the
real ASGI app and the real database, and additionally exercises the
reconciliation function against injected, fully-cleaned-up fixtures to prove
BOTH drift directions actually get fixed (since live data has none to
exercise it against for real).

Hydrates DATABASE_URL from Doppler over HTTPS at startup — run_sprint.sh's
Step 3 does not `doppler run --` this script.

Run:  python3 apps/api/scripts/verify_orgadminwrites.py
"""
import asyncio
import pathlib
import sys
from uuid import UUID

HERE = pathlib.Path(__file__).resolve()
sys.path.insert(0, str(HERE.parent))
from _db_bootstrap import bootstrap_async  # noqa: E402
from _reconcile_orgadminwrites_drift import find_drift, reconcile  # noqa: E402

HEADERS = {"Authorization": "Bearer verify-token"}

ORG = UUID("00000000-0000-0000-0000-000000000001")            # 2nd Act Capital
HOLLIS = UUID("bb347258-8f28-4f49-8cc9-e29ccad82884")          # Hollisworks
MEMBER_ROLE_ID = UUID("00000000-0000-0000-0000-000000000013")  # real, deployed 'member' role (2nd Act)
ORG_ADMIN_ROLE_ID = UUID("fb2b9fa9-6189-4224-b365-322bdce758ef")  # real, deployed 'org_admin' role (2nd Act)

REAL_ORG_ADMIN_ID = UUID("a35f8681-cdc8-40bc-b5d6-df5197963af4")  # jpl99172@gmail.com — READ-ONLY

# This script's own fixtures (namespaced 99300000..., created + torn down here).
FIXTURE_ACTOR_ID = UUID("99300000-0000-0000-0000-0000000c0a01")     # super_admin, performs invite/assign
FIXTURE_ACTOR_SUB = "auth0|verify_orgadminwrites_actor"
FIXTURE_PROMOTE_ID = UUID("99300000-0000-0000-0000-0000000c0a02")   # plain member, promoted then demoted
FIXTURE_LEGACY_BUG_ID = UUID("99300000-0000-0000-0000-0000000c0a03")  # Task 1d + Task 3 direction 1
FIXTURE_GRANT_ONLY_ID = UUID("99300000-0000-0000-0000-0000000c0a04")  # Task 3 direction 2

ORGADMIN_INVITE_EMAIL = "verify_orgadminwrites_orgadmin_invite@test.local"
NORMAL_INVITE_EMAIL = "verify_orgadminwrites_normal_invite@test.local"

_ok = True
_n_pass = 0
_n_fail = 0
_finds = []


def check(label, passed, detail=""):
    global _ok, _n_pass, _n_fail
    print(f"{'[PASS]' if passed else '[FAIL]'} {label}" + (f"  — {detail}" if detail else ""))
    if passed:
        _n_pass += 1
    else:
        _n_fail += 1
        _ok = False
    return passed


def find(label, detail=""):
    print(f"[FIND] {label}" + (f"  — {detail}" if detail else ""))
    _finds.append(label)


class _Principal:
    """Drives the real ASGI app as one user — same pattern as
    verify_orgadminrole.py. ``sub`` may be a real user row's own UUID
    (``services.users.ensure_user`` rule 2 matches a sub that is itself a
    UUID to that row's id directly) — this is how an INVITED-BUT-NOT-YET-
    ENROLLED user (auth0_sub IS NULL) can still be driven through the real
    app without needing an actual Auth0 enrollment round-trip.
    """

    def __init__(self, client, sub):
        self.client, self.sub = client, sub

    def call(self, method, path, body=None):
        import main
        sub = self.sub
        main.verify_token = lambda _t: {"sub": sub, "email": f"{sub}@test.local"}
        fn = getattr(self.client, method)
        return fn(path, headers=HEADERS, **({"json": body} if body is not None else {}))


async def setup_fixtures(pool):
    from services.database import reset_rls_context, set_rls_context

    tokens = set_rls_context(None, True)
    try:
        async with pool.acquire() as conn:
            # Actor: real controlled auth0_sub (unlike the two real production
            # super_admin accounts, which have NULL auth0_sub — see
            # verify_orgadminrole.py's note on why that breaks the RLS-layer
            # is_super_admin check specifically). Needed here because this
            # actor performs real WRITES (invite, assign_role).
            await conn.execute(
                """
                INSERT INTO users (id, org_id, email, full_name, auth0_sub, role)
                VALUES ($1, $2, $3, 'Verify OrgAdminWrites Actor', $4, 'super_admin')
                ON CONFLICT (id) DO UPDATE SET auth0_sub = EXCLUDED.auth0_sub
                """,
                FIXTURE_ACTOR_ID, ORG, "verify_orgadminwrites_actor@test.local",
                FIXTURE_ACTOR_SUB,
            )
            # Promotion target: an ordinary member, zero RBAC roles at first.
            await conn.execute(
                """
                INSERT INTO users (id, org_id, email, full_name, role)
                VALUES ($1, $2, $3, 'Verify OrgAdminWrites Promote Target', 'member')
                ON CONFLICT (id) DO NOTHING
                """,
                FIXTURE_PROMOTE_ID, ORG, "verify_orgadminwrites_promote@test.local",
            )
            # Task 1d / Task 3 direction 1: role string says org_admin, ZERO
            # grants — the exact pre-Task-2 shape of the invite-path bug.
            await conn.execute(
                """
                INSERT INTO users (id, org_id, email, full_name, role)
                VALUES ($1, $2, $3, 'Verify OrgAdminWrites Legacy Bug', 'org_admin')
                ON CONFLICT (id) DO NOTHING
                """,
                FIXTURE_LEGACY_BUG_ID, ORG, "verify_orgadminwrites_legacybug@test.local",
            )
            # Task 3 direction 2: a REAL org_admin grant, but users.role still
            # says 'member' — the exact pre-Task-2 shape of the assign_role
            # gap (promotion granting without ever syncing the string).
            await conn.execute(
                """
                INSERT INTO users (id, org_id, email, full_name, role)
                VALUES ($1, $2, $3, 'Verify OrgAdminWrites Grant Only', 'member')
                ON CONFLICT (id) DO NOTHING
                """,
                FIXTURE_GRANT_ONLY_ID, ORG, "verify_orgadminwrites_grantonly@test.local",
            )
            await conn.execute(
                "INSERT INTO user_roles (user_id, role_id) VALUES ($1, $2) ON CONFLICT DO NOTHING",
                FIXTURE_GRANT_ONLY_ID, ORG_ADMIN_ROLE_ID,
            )
    finally:
        reset_rls_context(tokens)


async def teardown(pool, extra_user_ids):
    from services.database import reset_rls_context, set_rls_context

    tokens = set_rls_context(None, True)
    try:
        async with pool.acquire() as conn:
            all_ids = [
                FIXTURE_ACTOR_ID, FIXTURE_PROMOTE_ID, FIXTURE_LEGACY_BUG_ID,
                FIXTURE_GRANT_ONLY_ID, *extra_user_ids,
            ]
            before = await conn.fetchval(
                "SELECT count(*) FROM users WHERE id = ANY($1::uuid[])", all_ids
            )
            # audit_log.user_id FKs to users (ON DELETE NO ACTION) — every
            # invite/assign_role write_audit_log call in this script used
            # `actor=FIXTURE_ACTOR_ID`, which lands in that column. Must clear
            # before deleting the actor row, per the standing FK-order rule.
            await conn.execute("DELETE FROM audit_log WHERE user_id = $1", FIXTURE_ACTOR_ID)
            # user_roles is ON DELETE CASCADE from users, so deleting the
            # users rows is sufficient for the grants.
            await conn.execute("DELETE FROM users WHERE id = ANY($1::uuid[])", all_ids)
            after = await conn.fetchval(
                "SELECT count(*) FROM users WHERE id = ANY($1::uuid[])", all_ids
            )
            after_audit = await conn.fetchval(
                "SELECT count(*) FROM audit_log WHERE user_id = $1", FIXTURE_ACTOR_ID
            )
    finally:
        reset_rls_context(tokens)
    check("[teardown] all fixture rows existed before cleanup",
          before == len(all_ids), f"before={before} expected={len(all_ids)}")
    check("[teardown] zero leftover fixture user rows", after == 0, f"count={after}")
    check("[teardown] zero leftover fixture audit_log rows", after_audit == 0, f"count={after_audit}")


async def main() -> int:
    url = await bootstrap_async()
    if not url:
        print("[BLOCKED] no working DATABASE_URL after Doppler hydration.")
        return 2

    sys.path.insert(0, str(HERE.parents[1]))  # apps/api on sys.path
    from services.database import close_pool, get_pool, reset_rls_context, set_rls_context
    from services.rbac import ORG_ADMIN_PERMISSION, has_org_admin_grant, has_permission

    pool = await get_pool()

    # ═══════════════════════════════════════════════════════════════════
    # TASK 1 — the four discovery findings.
    # ═══════════════════════════════════════════════════════════════════
    print("\n=== TASK 1 FINDINGS ===\n")

    print("[1a] Every place in deployed code that WRITES users.role, any value:")
    for line in [
        "  services/invites.py create_invite() — INSERT ... role (ALLOWED_INVITE_ROLES:"
        " 'member'/'org_admin'). THE PRIMARY GAP: wrote 'org_admin' with no grant. FIXED"
        " this sprint (Task 2) — now grants inside the same transaction.",
        "  routers/admin.py assign_role() (PUT /admin/users/{id}/role) — did NOT write"
        " users.role at all before this sprint; it only wrote user_roles. THE REVERSE"
        " GAP: a promotion via the RBAC role dropdown granted org_admin without ever"
        " setting the string, and a demotion left a stale 'org_admin' string behind"
        " with the grant already gone. FIXED this sprint — now syncs the string in"
        " both directions, guarded to never touch a 'super_admin' string.",
        "  routers/admin.py delete_user() (anonymization) — already revoked user_roles"
        " unconditionally but left users.role untouched, which would have created NEW"
        " drift going forward (an anonymized former org_admin reading as an admin with"
        " zero grants). FIXED this sprint — same lockstep rule, additive to the"
        " existing anonymize UPDATE.",
        "  services/users.py ensure_user() — writes 'super_admin' (Hollisworks-issuer"
        " detection) or 'member' (new row default). NEVER writes 'org_admin'. Out of"
        " scope: super_admin is a separate axis, untouched by RBAC"
        " (services.rbac.is_super_admin), confirmed unaffected by orgadminrole.structural.",
        "  apps/api/scripts/_apply_orgadminrole_migration.py — one-time, additive-only,"
        " already run and merged; does not write users.role at all (grants only). Not a"
        " live write path.",
    ]:
        print(line)

    print("\n[1b] Per path, did it create a matching grant BEFORE this sprint's fix?")
    for line in [
        "  create_invite: NO — wrote the string, created zero user_roles rows.",
        "  assign_role: PARTIALLY BACKWARDS — it DID write a real grant (the RBAC"
        " mechanism itself was correct), but never wrote the matching users.role"
        " string, so a promoted admin's account_role display and every legacy"
        " users.role-string reader (routers/admin.py's own _forbid_staff_target,"
        " routers/users.py's account_role field) stayed stale.",
        "  delete_user: correctly revoked the grant, but (before this sprint) left"
        " the string stale — the string-side half of the same lockstep gap.",
    ]:
        print(line)

    print("\n[1c] The real, existing pattern this codebase uses to grant a role, reused"
          " (not reinvented):")
    print("  scripts/_apply_orgadminrole_migration.py's one-time logic — ensure the"
          " permission row, ensure the per-org role row (roles.org_id is part of a"
          " (org_id, name) UNIQUE key), grant the permission to the role, grant the"
          " role to the user, every statement ON CONFLICT DO NOTHING. This sprint"
          " promoted that exact shape into reusable services.rbac helpers"
          " (ensure_permission/ensure_role/ensure_org_admin_role/grant_org_admin/"
          "revoke_org_admin/has_org_admin_grant) so it can run on every write, not"
          " only once at migration time. services/invites.py and routers/admin.py"
          " both call these — no second grant mechanism.")

    print("\n[1d] What happens TODAY if someone is invited as org_admin — the REAL"
          " failure mode, proven live (not hypothetical):")
    tokens = set_rls_context(None, True)
    try:
        await setup_fixtures(pool)
        async with pool.acquire() as conn:
            # FIXTURE_LEGACY_BUG_ID reproduces the PRE-FIX shape exactly:
            # role='org_admin', zero user_roles rows at all.
            zero_roles = await conn.fetchval(
                "SELECT count(*) FROM user_roles WHERE user_id = $1", FIXTURE_LEGACY_BUG_ID
            )
        check("[1d setup] legacy-bug fixture genuinely has ZERO role rows (the exact"
              " pre-fix shape)", zero_roles == 0, f"count={zero_roles}")

        allow_before = await has_permission(pool, FIXTURE_LEGACY_BUG_ID, ORG, ORG_ADMIN_PERMISSION)
        check("[1d] NOT simply 'refused': has_permission's zero-role default-allow"
              " bootstrap means a freshly invited org_admin with no grant is initially"
              " OVER-privileged (default-allow to EVERY permission, not just"
              " manage_org_settings) — True here", allow_before is True)
        find("1d: the prompt's framing ('will be REFUSED') is only half the real story."
             " A user invited as org_admin with the pre-fix code has ZERO user_roles"
             " rows, and has_permission default-allows a user with no roles at all"
             " (the documented single-admin-bootstrap posture) — so they are NOT"
             " immediately refused, they are silently over-privileged to every"
             " permission in the system. The refusal only manifests the first time"
             " ANY role gets assigned to them (a normal event — e.g. being given the"
             " ordinary 'member' RBAC role, which is routine onboarding elsewhere in"
             " this app) — at that point has_permission's strict per-permission check"
             " engages, and since org_admin was never actually granted, they lose"
             " manage_org_settings and every org-admin surface refuses them, despite"
             " users.role still literally saying 'org_admin'. Proven below by giving"
             " this fixture the ordinary 'member' role and re-checking.")

        async with pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO user_roles (user_id, role_id) VALUES ($1, $2) ON CONFLICT DO NOTHING",
                FIXTURE_LEGACY_BUG_ID, MEMBER_ROLE_ID,
            )
        allow_after = await has_permission(pool, FIXTURE_LEGACY_BUG_ID, ORG, ORG_ADMIN_PERMISSION)
        check("[1d] the moment ANY role is assigned, the strict check engages and this"
              " user IS refused manage_org_settings despite users.role='org_admin' —"
              " the real failure this sprint's fix prevents", allow_after is False)

        print("\n[1e] Live org counts, confirmed against Task 1 of orgadminrole.structural:")
        async with pool.acquire() as conn:
            counts = await conn.fetch(
                """
                SELECT o.name, count(u.id) AS users,
                       count(*) FILTER (WHERE u.role = 'org_admin') AS role_string_admins
                FROM organizations o LEFT JOIN users u ON u.org_id = o.id
                GROUP BY o.name ORDER BY o.name
                """
            )
        print(f"  {[dict(r) for r in counts]}")
    finally:
        reset_rls_context(tokens)

    # ═══════════════════════════════════════════════════════════════════
    # TASK 4 — real proof, through the real ASGI app.
    # ═══════════════════════════════════════════════════════════════════
    print("\n=== TASK 4: REAL PROOF ===\n")
    invited_ids = []
    try:
        from starlette.testclient import TestClient
        import main as main_module

        await close_pool()  # rebind pool onto TestClient's own event loop (verify_orgadminrole.py pattern)
        client = TestClient(main_module.app, raise_server_exceptions=False)
        client.__enter__()
        try:
            actor = _Principal(client, FIXTURE_ACTOR_SUB)
            promote_target = _Principal(client, str(FIXTURE_PROMOTE_ID))

            # -- invite as org_admin --
            r = actor.call("post", "/api/v1/admin/invites",
                            {"email": ORGADMIN_INVITE_EMAIL, "full_name": "OrgAdmin Invitee",
                             "role": "org_admin"})
            check("[Task4] POST /admin/invites role=org_admin -> 201", r.status_code == 201,
                  f"HTTP {r.status_code} body={r.text[:300]}")
            invited_org_admin_id = UUID(r.json()["id"]) if r.status_code == 201 else None
            if invited_org_admin_id:
                invited_ids.append(invited_org_admin_id)

            # -- invite as plain member --
            r = actor.call("post", "/api/v1/admin/invites",
                            {"email": NORMAL_INVITE_EMAIL, "full_name": "Normal Invitee",
                             "role": "member"})
            check("[Task4] POST /admin/invites role=member -> 201", r.status_code == 201,
                  f"HTTP {r.status_code} body={r.text[:300]}")
            invited_member_id = UUID(r.json()["id"]) if r.status_code == 201 else None
            if invited_member_id:
                invited_ids.append(invited_member_id)
        finally:
            client.__exit__(None, None, None)

        await close_pool()
        pool = await get_pool()

        # -- independent read-back: BOTH the string and the real grant --
        tokens = set_rls_context(None, True)
        try:
            async with pool.acquire() as conn:
                if invited_org_admin_id:
                    role_str = await conn.fetchval(
                        "SELECT role FROM users WHERE id = $1", invited_org_admin_id
                    )
                    check("[Task4] invited org_admin: users.role == 'org_admin' (independent read)",
                          role_str == "org_admin", f"role={role_str!r}")
                    has_grant = await has_org_admin_grant(conn, invited_org_admin_id, ORG)
                    check("[Task4] invited org_admin: REAL user_roles grant exists in org A"
                          " (independent read, not inspecting the response)", has_grant is True)
                    has_grant_hollis = await has_org_admin_grant(conn, invited_org_admin_id, HOLLIS)
                    check("[Task4] CROSS-ORG: invited org_admin's grant is scoped to org A"
                          " ONLY — no grant in Hollisworks", has_grant_hollis is False)
                if invited_member_id:
                    has_grant_normal = await has_org_admin_grant(conn, invited_member_id, ORG)
                    check("[Task4] a normal (role=member) invite creates NO org_admin grant",
                          has_grant_normal is False)
        finally:
            reset_rls_context(tokens)

        # -- reach a real org-admin endpoint through the real app --
        # Every NEW TestClient() spins up its own portal-thread event loop;
        # the pool from the previous block is bound to a loop that has
        # already torn down, so it must be closed (and lazily recreated
        # inside the NEW loop by the app's own get_pool() calls) before each
        # fresh TestClient __enter__ — not just once at the very start
        # (verify_orgadminrole.py's pattern, applied at every transition here
        # since this script uses three separate TestClient sessions).
        await close_pool()
        client = TestClient(main_module.app, raise_server_exceptions=False)
        client.__enter__()
        try:
            if invited_org_admin_id:
                invitee = _Principal(client, str(invited_org_admin_id))
                r = invitee.call("get", "/api/v1/modeling/ta/defaults")
                can_write = (r.json() or {}).get("permissions", {}).get("can_write") if r.status_code == 200 else None
                check("[Task4] invited org_admin reaches GET /modeling/ta/defaults with"
                      " can_write=True — proves the LIVE break is closed end-to-end,"
                      " not just at the grant-table level",
                      r.status_code == 200 and can_write is True,
                      f"HTTP {r.status_code} can_write={can_write}")

                r = invitee.call("put", f"/api/v1/orgs/{HOLLIS}/settings/brand.short_name",
                                  body={"value": "Hollisworks"})
                check("[Task4] CROSS-ORG: invited org_admin -> PUT Hollisworks settings -> 403"
                      " (scoped to their own org, no row written)", r.status_code == 403,
                      f"HTTP {r.status_code}")

            # -- promotion via PUT /admin/users/{id}/role --
            actor = _Principal(client, FIXTURE_ACTOR_SUB)
            r = actor.call("put", f"/api/v1/admin/users/{FIXTURE_PROMOTE_ID}/role",
                            {"role_id": str(ORG_ADMIN_ROLE_ID)})
            check("[Task4] PUT /admin/users/{id}/role -> org_admin -> 200", r.status_code == 200,
                  f"HTTP {r.status_code} body={r.text[:300]}")

            promote_target = _Principal(client, str(FIXTURE_PROMOTE_ID))
            r = promote_target.call("get", "/api/v1/modeling/ta/defaults")
            can_write_promoted = (r.json() or {}).get("permissions", {}).get("can_write") if r.status_code == 200 else None
            check("[Task4] promoted user reaches org-admin endpoint (can_write=True) —"
                  " through the real app", r.status_code == 200 and can_write_promoted is True,
                  f"HTTP {r.status_code} can_write={can_write_promoted}")
        finally:
            client.__exit__(None, None, None)

        await close_pool()
        pool = await get_pool()

        tokens = set_rls_context(None, True)
        try:
            async with pool.acquire() as conn:
                role_str = await conn.fetchval("SELECT role FROM users WHERE id = $1", FIXTURE_PROMOTE_ID)
                check("[Task4] promotion set users.role == 'org_admin' (independent read)",
                      role_str == "org_admin", f"role={role_str!r}")
                has_grant = await has_org_admin_grant(conn, FIXTURE_PROMOTE_ID, ORG)
                check("[Task4] promotion created a REAL user_roles grant", has_grant is True)
        finally:
            reset_rls_context(tokens)

        # -- demotion via the same endpoint --
        await close_pool()
        client = TestClient(main_module.app, raise_server_exceptions=False)
        client.__enter__()
        try:
            actor = _Principal(client, FIXTURE_ACTOR_SUB)
            r = actor.call("put", f"/api/v1/admin/users/{FIXTURE_PROMOTE_ID}/role",
                            {"role_id": str(MEMBER_ROLE_ID)})
            check("[Task4] PUT /admin/users/{id}/role -> member (demotion) -> 200",
                  r.status_code == 200, f"HTTP {r.status_code} body={r.text[:300]}")

            promote_target = _Principal(client, str(FIXTURE_PROMOTE_ID))
            r = promote_target.call("get", "/api/v1/modeling/ta/defaults")
            can_write_demoted = (r.json() or {}).get("permissions", {}).get("can_write") if r.status_code == 200 else None
            check("[Task4] demoted user's org-admin access is GENUINELY LOST"
                  " (can_write=False) — proven through the real app, not by inspecting"
                  " grants alone", r.status_code == 200 and can_write_demoted is False,
                  f"HTTP {r.status_code} can_write={can_write_demoted}")
        finally:
            client.__exit__(None, None, None)

        await close_pool()
        pool = await get_pool()

        tokens = set_rls_context(None, True)
        try:
            async with pool.acquire() as conn:
                role_str = await conn.fetchval("SELECT role FROM users WHERE id = $1", FIXTURE_PROMOTE_ID)
                check("[Task4] demotion RESET users.role to 'member' (independent read) —"
                      " no stale 'org_admin' string left behind", role_str == "member",
                      f"role={role_str!r}")
                has_grant = await has_org_admin_grant(conn, FIXTURE_PROMOTE_ID, ORG)
                check("[Task4] demotion REMOVED the real user_roles grant", has_grant is False)
        finally:
            reset_rls_context(tokens)

        # ═══════════════════════════════════════════════════════════════
        # TASK 3 — drift reconciliation, proven both directions + zero-drift.
        # ═══════════════════════════════════════════════════════════════
        print("\n=== TASK 3: DRIFT RECONCILIATION ===\n")
        tokens = set_rls_context(None, True)
        try:
            async with pool.acquire() as conn:
                string_only_before, grant_only_before = await find_drift(conn)
            before_ids_1 = {r["id"] for r in string_only_before}
            before_ids_2 = {r["id"] for r in grant_only_before}
            check("[Task3] direction-1 drift fixture (string, no grant) is detected before"
                  " reconciliation", FIXTURE_LEGACY_BUG_ID in before_ids_1)
            check("[Task3] direction-2 drift fixture (grant, wrong string) is detected"
                  " before reconciliation", FIXTURE_GRANT_ONLY_ID in before_ids_2)
            check("[Task3] the ONE real, live org_admin holder shows as drift-free before"
                  " reconciliation (already in sync, per _reconcile_orgadminwrites_drift.py's"
                  " real run this sprint: 0 found)",
                  REAL_ORG_ADMIN_ID not in before_ids_1 and REAL_ORG_ADMIN_ID not in before_ids_2)

            async with pool.acquire() as conn:
                async with conn.transaction():
                    result = await reconcile(conn)
            granted_ids = {UUID(r["id"]) for r in result["granted"]}
            stringed_ids = {UUID(r["id"]) for r in result["stringed"]}
            print(f"[Task3] reconcile() granted: {[r['email'] for r in result['granted']]}")
            print(f"[Task3] reconcile() re-stringed: {[r['email'] for r in result['stringed']]}")
            check("[Task3] direction-1 fixture GRANTED by reconciliation",
                  FIXTURE_LEGACY_BUG_ID in granted_ids)
            check("[Task3] direction-2 fixture RE-STRINGED by reconciliation",
                  FIXTURE_GRANT_ONLY_ID in stringed_ids)
            check("[Task3] the real live holder was NOT touched (already in sync)",
                  REAL_ORG_ADMIN_ID not in granted_ids and REAL_ORG_ADMIN_ID not in stringed_ids)

            async with pool.acquire() as conn:
                has_grant_1 = await has_org_admin_grant(conn, FIXTURE_LEGACY_BUG_ID, ORG)
                role_2 = await conn.fetchval("SELECT role FROM users WHERE id = $1", FIXTURE_GRANT_ONLY_ID)
            check("[Task3] direction-1 fixture now HAS the grant (independent read)",
                  has_grant_1 is True)
            check("[Task3] direction-2 fixture's string is now 'org_admin' (independent read)",
                  role_2 == "org_admin")

            async with pool.acquire() as conn:
                string_only_after, grant_only_after = await find_drift(conn)
            check("[Task3] ZERO drift remains anywhere in the table after reconciliation —"
                  " no string without its grant, no grant without its string, in EITHER"
                  " direction", len(string_only_after) == 0 and len(grant_only_after) == 0,
                  f"string_only={[str(r['id']) for r in string_only_after]} "
                  f"grant_only={[str(r['id']) for r in grant_only_after]}")
        finally:
            reset_rls_context(tokens)

    finally:
        await teardown(pool, invited_ids)
        # Re-confirm the real, live table is genuinely back to zero drift —
        # not merely that the fixtures are gone.
        tokens = set_rls_context(None, True)
        try:
            async with pool.acquire() as conn:
                string_only_final, grant_only_final = await find_drift(conn)
        finally:
            reset_rls_context(tokens)
        check("[teardown] zero drift remains in the REAL table after fixture cleanup"
              " (matches the pre-test baseline this sprint's own Task 3 run already"
              " established live)", len(string_only_final) == 0 and len(grant_only_final) == 0)
        await pool.close()

    print(f"\n{'=' * 60}\n{_n_pass} PASS, {_n_fail} FAIL, {len(_finds)} FIND\n{'=' * 60}")
    return 0 if _ok else 1


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except Exception:
        import traceback
        traceback.print_exc()
        sys.exit(2)
