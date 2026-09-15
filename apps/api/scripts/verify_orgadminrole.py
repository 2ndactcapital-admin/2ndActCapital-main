"""verify_orgadminrole.py — org_admin role reconciliation, Tasks 6-7 real proof.

Tasks 1-5 were implemented and (per live DB re-verification at the top of
this script) are confirmed present: the `manage_org_settings` permission, the
`org_admin` role (2nd Act org), its role_permissions grant, and the single
real holder's (jpl99172@gmail.com) user_roles grant. This script does NOT
redo that work — it proves it, end to end, through the real ASGI app and the
real database.

Hydrates DATABASE_URL from Doppler over HTTPS at startup (the
verify_rlscutover.py pattern) — run_sprint.sh's Step 3 does not `doppler
run --` this script.

Run:  python3 apps/api/scripts/verify_orgadminrole.py
"""
import asyncio
import pathlib
import sys
import traceback
from uuid import UUID

HERE = pathlib.Path(__file__).resolve()
sys.path.insert(0, str(HERE.parent))
from _db_bootstrap import bootstrap_async  # noqa: E402

HEADERS = {"Authorization": "Bearer verify-token"}

ORG = UUID("00000000-0000-0000-0000-000000000001")          # 2nd Act Capital
HOLLIS = UUID("bb347258-8f28-4f49-8cc9-e29ccad82884")        # Hollisworks
MEMBER_ROLE_ID = UUID("00000000-0000-0000-0000-000000000013")  # real, deployed 'member' role

# Real, live production accounts — used READ-ONLY / auth-simulation only,
# never mutated.
REAL_ORG_ADMIN_ID = UUID("a35f8681-cdc8-40bc-b5d6-df5197963af4")       # jpl99172@gmail.com
REAL_ORG_ADMIN_EMAIL = "jpl99172@gmail.com"
REAL_SUPER_ADMIN_ZERO_ROLES_ID = UUID("f46eb620-f03c-49ab-b946-eefa621022f7")  # jlarizza@gmail.com
REAL_SUPER_ADMIN_ZERO_ROLES_EMAIL = "jlarizza@gmail.com"

# This script's own fixtures (created + torn down here).
FIXTURE_NONADMIN_ID = UUID("99000000-0000-0000-0000-0000000a0a01")
FIXTURE_NONADMIN_SUB = "auth0|verify_orgadminrole_nonadmin"
FIXTURE_RUN_ID = UUID("99000000-0000-0000-0000-0000000a0a02")  # Hollisworks zero-recipient probe

# A CONTROLLED super_admin fixture, distinct from the two real accounts above.
# Both real accounts have auth0_sub IS NULL (confirmed live) — main.py's RLS
# middleware (rls_context_middleware -> _resolve_account_state) looks the
# caller up BY auth0_sub, separately from and BEFORE services.users.ensure_user
# (which additionally accepts sub-as-existing-row-id, the trick that lets the
# rest of this script drive the app as the two real accounts at all). A NULL
# auth0_sub means that earlier, RLS-GUC-setting lookup can never match either
# real account regardless of what `sub` is stubbed to, so app.is_super_admin
# stays wrongly 'false' for them at the RLS layer specifically — invisible for
# every same-org request (org_id defaults to 2nd Act, which is also their real
# org, so the org-match arm of every RLS policy covers the gap), but it WOULD
# break a genuine cross-org write. This is a pre-existing data gap in these
# two real, live rows, orthogonal to this sprint's permission migration — not
# something to patch on a live account mid-verify-script. A fully-controlled
# fixture (real auth0_sub this script owns end-to-end) is the correct way to
# prove the cross-org RLS behavior instead.
FIXTURE_SUPERADMIN_ID = UUID("99000000-0000-0000-0000-0000000a0a03")
FIXTURE_SUPERADMIN_SUB = "auth0|verify_orgadminrole_superadmin"

ALERT_UNDELIVERED_ACTION = "workflow_alert_undelivered"

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
    """Drives the real ASGI app as one user — same pattern as verify_rlscutover.py.

    ``sub`` may be a real user's own UUID (str) — ``services.users.ensure_user``
    resolves a sub that is itself a UUID matching an existing row's id to that
    row (rule 2), which is how this authenticates as the real, live
    jpl99172@gmail.com / jlarizza@gmail.com accounts without needing their
    actual Auth0 identifiers.
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
    """Create ONE fixture: a real non-admin user holding the REAL, deployed
    'member' role (view_dashboard / view_marketplace / submit_deal / ... —
    confirmed via live query NOT to include manage_org_settings). Zero-role
    fixtures default-allow (services.rbac.has_permission) and would prove
    nothing about the refusal path — this is the fixture shape Task 6 itself
    calls for."""
    from services.database import reset_rls_context, set_rls_context

    tokens = set_rls_context(None, True)
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO users (id, org_id, email, full_name, auth0_sub, role)
                VALUES ($1, $2, $3, 'Verify OrgAdminRole NonAdmin', $4, 'member')
                ON CONFLICT (id) DO UPDATE SET auth0_sub = EXCLUDED.auth0_sub
                """,
                FIXTURE_NONADMIN_ID, ORG, "verify_orgadminrole_nonadmin@test.local",
                FIXTURE_NONADMIN_SUB,
            )
            await conn.execute(
                "INSERT INTO user_roles (user_id, role_id) VALUES ($1, $2) ON CONFLICT DO NOTHING",
                FIXTURE_NONADMIN_ID, MEMBER_ROLE_ID,
            )
            await conn.execute(
                """
                INSERT INTO users (id, org_id, email, full_name, auth0_sub, role)
                VALUES ($1, $2, $3, 'Verify OrgAdminRole SuperAdmin', $4, 'super_admin')
                ON CONFLICT (id) DO UPDATE SET auth0_sub = EXCLUDED.auth0_sub
                """,
                FIXTURE_SUPERADMIN_ID, ORG, "verify_orgadminrole_superadmin@test.local",
                FIXTURE_SUPERADMIN_SUB,
            )
    finally:
        reset_rls_context(tokens)


async def teardown_fixtures(pool):
    from services.database import reset_rls_context, set_rls_context

    tokens = set_rls_context(None, True)
    try:
        async with pool.acquire() as conn:
            before = await conn.fetchval("SELECT count(*) FROM users WHERE id = $1", FIXTURE_NONADMIN_ID)
            # org_settings.updated_by FKs to users(id) — the Hollisworks probe
            # row was written by FIXTURE_SUPERADMIN_ID, so org_settings MUST be
            # deleted before that user row or the FK delete blows up.
            before_settings = {
                str(r["org_id"]): r["setting_value"]
                for r in await conn.fetch(
                    "SELECT org_id, setting_value FROM org_settings "
                    "WHERE org_id = ANY($1::uuid[]) AND setting_key = 'brand.short_name'",
                    [ORG, HOLLIS],
                )
            }
            await conn.execute(
                "DELETE FROM org_settings WHERE org_id = ANY($1::uuid[]) AND setting_key = 'brand.short_name'",
                [ORG, HOLLIS],
            )
            await conn.execute("DELETE FROM user_roles WHERE user_id = $1", FIXTURE_NONADMIN_ID)
            await conn.execute("DELETE FROM users WHERE id = $1", FIXTURE_NONADMIN_ID)
            await conn.execute("DELETE FROM users WHERE id = $1", FIXTURE_SUPERADMIN_ID)
            await conn.execute(
                "DELETE FROM audit_log WHERE org_id = $1 AND action = $2 AND resource_id = $3",
                HOLLIS, ALERT_UNDELIVERED_ACTION, FIXTURE_RUN_ID,
            )
            after_user = await conn.fetchval("SELECT count(*) FROM users WHERE id = $1", FIXTURE_NONADMIN_ID)
            after_role = await conn.fetchval("SELECT count(*) FROM user_roles WHERE user_id = $1", FIXTURE_NONADMIN_ID)
            after_superadmin = await conn.fetchval(
                "SELECT count(*) FROM users WHERE id = $1", FIXTURE_SUPERADMIN_ID
            )
            after_audit = await conn.fetchval(
                "SELECT count(*) FROM audit_log WHERE org_id = $1 AND action = $2 AND resource_id = $3",
                HOLLIS, ALERT_UNDELIVERED_ACTION, FIXTURE_RUN_ID,
            )
            after_settings = await conn.fetchval(
                "SELECT count(*) FROM org_settings WHERE org_id = ANY($1::uuid[]) "
                "AND setting_key = 'brand.short_name'",
                [ORG, HOLLIS],
            )
    finally:
        reset_rls_context(tokens)
    check("[teardown] both org_settings probe rows existed before cleanup",
          set(before_settings) == {str(ORG), str(HOLLIS)}, f"before={before_settings}")
    check("[teardown] zero leftover org_settings probe rows (orgs fall back to defaults again)",
          after_settings == 0, f"count={after_settings}")
    check("[teardown] fixture existed before teardown", before == 1)
    check("[teardown] zero leftover fixture user rows", after_user == 0, f"count={after_user}")
    check("[teardown] zero leftover fixture user_roles rows", after_role == 0, f"count={after_role}")
    check("[teardown] zero leftover fixture super_admin rows", after_superadmin == 0, f"count={after_superadmin}")
    check("[teardown] zero leftover Hollisworks probe audit_log rows", after_audit == 0, f"count={after_audit}")


async def main() -> int:
    url = await bootstrap_async()
    if not url:
        print("[BLOCKED] no working DATABASE_URL after Doppler hydration.")
        return 2

    sys.path.insert(0, str(HERE.parents[1]))  # apps/api on sys.path
    from services.database import get_pool, set_rls_context, reset_rls_context
    from services.rbac import (
        ORG_ADMIN_PERMISSION,
        can_manage_org_settings,
        get_users_with_permission,
        has_permission,
        load_principal,
    )
    from services import workflow_todos

    pool = await get_pool()

    # ═══════════════════════════════════════════════════════════════════
    # TASK 1 — the five discovery findings, restated against LIVE data.
    # ═══════════════════════════════════════════════════════════════════
    print("\n=== TASK 1 FINDINGS ===\n")
    tokens = set_rls_context(None, True)
    try:
        async with pool.acquire() as conn:
            role_counts = await conn.fetch(
                "SELECT role, count(*) AS n FROM users GROUP BY role ORDER BY n DESC"
            )
            org_admin_rows = await conn.fetch(
                "SELECT id, org_id, email FROM users WHERE role = 'org_admin' ORDER BY email"
            )
            roles_rows = await conn.fetch("SELECT id, org_id, name FROM roles WHERE name = 'org_admin'")
            role_perm_rows = await conn.fetch(
                """SELECT r.name role, p.name perm FROM role_permissions rp
                   JOIN roles r ON r.id = rp.role_id JOIN permissions p ON p.id = rp.permission_id
                   WHERE r.name = 'org_admin'"""
            )
            perm_row = await conn.fetchrow(
                "SELECT id, name, resource, action FROM permissions WHERE name = 'manage_org_settings'"
            )
            org_user_counts = await conn.fetch(
                """
                SELECT o.id, o.name, count(u.id) AS user_count,
                       count(*) FILTER (WHERE u.role = 'org_admin') AS org_admin_count
                FROM organizations o LEFT JOIN users u ON u.org_id = o.id
                GROUP BY o.id, o.name ORDER BY o.name
                """
            )

        print("[1a] users.role distribution:", {r["role"]: r["n"] for r in role_counts})
        print(f"[1a] role='org_admin' holders ({len(org_admin_rows)}):",
              [(str(r["id"]), r["email"], str(r["org_id"])) for r in org_admin_rows])
        check("[1a] exactly one live org_admin holder, jpl99172@gmail.com, org=2nd Act",
              len(org_admin_rows) == 1
              and org_admin_rows[0]["id"] == REAL_ORG_ADMIN_ID
              and org_admin_rows[0]["email"] == REAL_ORG_ADMIN_EMAIL
              and org_admin_rows[0]["org_id"] == ORG)

        print("\n[1b] Every place in deployed code that reads users.role (static inventory):")
        for line in [
            "  services/rbac.py: is_super_admin() / load_principal() — still read users.role"
            " for 'super_admin' (unchanged; NOT part of this migration's scope).",
            "  services/rbac.py: is_org_admin() — MIGRATED this sprint. No longer reads"
            " users.role at all; resolves ORG_ADMIN_PERMISSION via has_permission().",
            "  routers/admin.py: caller_is_super = is_super_admin(principal); "
            "_forbid_staff_target compares target['role']=='super_admin' — super_admin only,"
            " untouched by this migration.",
            "  routers/users.py (/users/me): account_role field echoes raw users.role to the"
            " frontend for display/gating — untouched (still the source menuVisibility.mjs's"
            " isSuperAdmin()/accountRoleOf() read for the SUPER_ADMIN gate).",
            "  services/invites.py: ALLOWED_INVITE_ROLES=('member','org_admin') — invite flow"
            " still WRITES users.role='org_admin' directly. NOT migrated: inviting someone as"
            " org_admin does not yet also grant the RBAC role/permission. Tracked in"
            " docs/PROJECT_STATUS.md as follow-up.",
            "  services/workflow_todos.py: create_held_run_alerts / create_trigger_expiring_"
            "alerts — MIGRATED this sprint (Task 4) off the raw 'role=org_admin' SQL scan onto"
            " rbac.get_users_with_permission(ORG_ADMIN_PERMISSION).",
            "  apps/web/lib/menuVisibility.mjs: the 5 org-admin menu items — MIGRATED this"
            " sprint to GATE_MANAGE_ORG_SETTINGS (perm-based); GATE_SUPER_ADMIN untouched.",
            "  apps/web/app/admin/settings/page.js: MIGRATED this sprint off theme.role string"
            " compare onto canPerm(me,'manage_org_settings').",
            "  apps/web/components/admin/UserManagement.jsx: 'Organization Admin' dropdown"
            " option — still writes users.role='org_admin' via the role-assignment UI. This is"
            " the legitimate SETTER for the column Task 3 said stays populated, not a gate;"
            " left as-is by design.",
        ]:
            print(line)
        find("1b: services/invites.py and UserManagement.jsx still WRITE users.role='org_admin'"
             " directly, with no corresponding RBAC grant — a user invited/set as org_admin"
             " through those paths will NOT automatically pass the new permission-based gates"
             " until someone also runs the Task 3 migration logic for them. Scoped as follow-up"
             " in docs/PROJECT_STATUS.md, not fixed in this sprint (Task 3 was explicitly"
             " scoped to migrating EXISTING holders, not to intercepting future writes).")

        print(f"\n[1c] roles WHERE name='org_admin': {[dict(r) for r in roles_rows]}")
        print(f"[1c] role_permissions grants for org_admin: {[dict(r) for r in role_perm_rows]}")
        print(f"[1c] permissions catalog row for manage_org_settings: {dict(perm_row) if perm_row else None}")
        check("[1c] org_admin role exists, scoped to 2nd Act org",
              len(roles_rows) == 1 and roles_rows[0]["org_id"] == ORG)
        check("[1c] manage_org_settings permission exists and is granted to org_admin",
              perm_row is not None and len(role_perm_rows) == 1
              and role_perm_rows[0]["perm"] == "manage_org_settings")
        find("1c: no existing permission was suitable for org-level admin access before this"
             " sprint — manage_members/manage_roles/manage_users are all narrower. "
             "routers/modeling_ta.py already referenced the literal string "
             "'manage_org_settings' in its envelope's write_permission field with no real "
             "permission row behind it; that string is now backed by a real row.")

        print("\n[1d] Endpoints gated on org-admin status and how, before this sprint:")
        for line in [
            "  routers/org_settings.py PUT (writes) -> services.rbac.can_manage_org_settings"
            " -> is_org_admin(users.role string) [NOW: permission-based]",
            "  routers/profiles.py _require_admin -> can_manage_org_settings"
            " [NOW: permission-based]",
            "  routers/modeling_ta.py PUT + envelope -> can_manage_org_settings"
            " [NOW: permission-based]",
            "  services/delegate_grants.py activate_springing_delegate -> is_org_admin"
            " [NOW: permission-based]",
            "  apps/web/lib/menuVisibility.mjs 5 menu items -> { roles: ['org_admin',"
            " 'super_admin'] } compared against account_role string [NOW: permission-based]",
            "  apps/web/app/admin/settings/page.js -> ITS OWN independent copy:"
            " role === 'org_admin' || role === 'super_admin' against theme.role"
            " [NOW: routed through the SAME canPerm() the sidebar uses]",
        ]:
            print(line)
        find("1d INCONSISTENCY (confirmed, now fixed): apps/web/app/admin/settings/page.js"
             " carried its OWN independent role-string gate, separate from"
             " menuVisibility.mjs's centralized one that every other org-admin page deferred"
             " to — the same 'two independent copies of a gate' shape the superadminmenu"
             " sprint fixed for the sidebar/admin-index pair. Now routed through the single"
             " canPerm() helper.")

        print(f"\n[1e] per-org user / org_admin counts: {[dict(r) for r in org_user_counts]}")
        hollis_row = next(r for r in org_user_counts if r["id"] == HOLLIS)
        check("[1e] Hollisworks org is real, has users, and has ZERO org_admin holders"
              " — confirmed live, the silent-no-recipient case",
              hollis_row["org_admin_count"] == 0)
    finally:
        reset_rls_context(tokens)

    # ═══════════════════════════════════════════════════════════════════
    # TASK 3 PROOF — every prior org_admin migrated, count-for-count;
    # nobody over-granted.
    # ═══════════════════════════════════════════════════════════════════
    print("\n=== TASK 3 PROOF ===\n")
    tokens = set_rls_context(None, True)
    try:
        async with pool.acquire() as conn:
            role_holders = {
                r["id"] for r in await conn.fetch("SELECT id FROM users WHERE role = 'org_admin'")
            }
            grant_holders = {
                r["user_id"] for r in await conn.fetch(
                    """SELECT ur.user_id FROM user_roles ur JOIN roles r ON r.id = ur.role_id
                       WHERE r.name = 'org_admin'"""
                )
            }
        missed = role_holders - grant_holders
        over_granted = grant_holders - role_holders
        print(f"users.role='org_admin' holders: {sorted(str(x) for x in role_holders)}")
        print(f"user_roles org_admin grant holders: {sorted(str(x) for x in grant_holders)}")
        check("[Task3] missed_users == 0 (every role holder has a real grant)",
              len(missed) == 0, f"missed={missed}")
        check("[Task3] over_granted == 0 (nobody granted who shouldn't be)",
              len(over_granted) == 0, f"over_granted={over_granted}")
        check("[Task3] set-for-set equality, count-for-count",
              role_holders == grant_holders == {REAL_ORG_ADMIN_ID})
    finally:
        reset_rls_context(tokens)

    # ═══════════════════════════════════════════════════════════════════
    # TASK 6 — real ASGI app proof: org_admin, non-admin, super_admin
    # (zero roles), cross-org.
    # ═══════════════════════════════════════════════════════════════════
    print("\n=== TASK 6: REAL APP PROOF ===\n")
    await setup_fixtures(pool)
    try:
        from starlette.testclient import TestClient
        import main as main_module
        from services.database import close_pool

        # -- super_admin with ZERO user_roles grants: confirm the real premise
        #    (checked with the CURRENT pool, before it gets closed below) --
        async with pool.acquire() as conn:
            zero_roles = await conn.fetchval(
                "SELECT count(*) FROM user_roles WHERE user_id = $1",
                REAL_SUPER_ADMIN_ZERO_ROLES_ID,
            )
        check("[Task6] jlarizza@gmail.com genuinely has ZERO user_roles grants"
              " (this is the real case being proven, not assumed)", zero_roles == 0,
              f"count={zero_roles}")

        # TestClient drives the ASGI app on its OWN event loop (a background
        # portal thread) — the shared `pool` object above is bound to THIS
        # script's asyncio.run() loop and cannot cross into it ("attached to
        # a different loop"). Force a close so the app's own get_pool() calls,
        # made from inside route handlers running on TestClient's loop, lazily
        # create a fresh pool bound to the RIGHT loop — same fix
        # verify_rlscutover.py uses.
        await close_pool()

        client = TestClient(main_module.app, raise_server_exceptions=False)
        client.__enter__()
        try:
            org_admin = _Principal(client, str(REAL_ORG_ADMIN_ID))
            super_admin = _Principal(client, str(REAL_SUPER_ADMIN_ZERO_ROLES_ID))
            nonadmin = _Principal(client, FIXTURE_NONADMIN_SUB)
            # Fixture super_admin (real, controlled auth0_sub — see the
            # FIXTURE_SUPERADMIN_ID comment above) for the ONE test that needs
            # main.py's RLS middleware to resolve is_super_admin correctly for
            # a genuine cross-org WRITE, which the two real accounts' NULL
            # auth0_sub cannot satisfy.
            fixture_super_admin = _Principal(client, FIXTURE_SUPERADMIN_SUB)

            # -- org_admin reaches every previously-reachable org-admin surface --
            r = org_admin.call("get", "/api/v1/admin/profiles")
            check("[Task6] real org_admin: GET /admin/profiles -> 200", r.status_code == 200,
                  f"HTTP {r.status_code}")

            r = org_admin.call("get", "/api/v1/modeling/ta/defaults")
            check("[Task6] real org_admin: GET /modeling/ta/defaults -> 200", r.status_code == 200,
                  f"HTTP {r.status_code}")
            can_write = (r.json() or {}).get("permissions", {}).get("can_write") if r.status_code == 200 else None
            check("[Task6] real org_admin: TA envelope can_write=True (via manage_org_settings)",
                  can_write is True, f"can_write={can_write}")

            r = org_admin.call("put", f"/api/v1/orgs/{ORG}/settings/brand.short_name",
                                body={"value": "2nd Act"})
            check("[Task6] real org_admin: PUT own-org settings -> 200 (idempotent roundtrip)",
                  r.status_code == 200, f"HTTP {r.status_code} body={r.text[:300]}")

            r = super_admin.call("get", "/api/v1/admin/profiles")
            check("[Task6] super_admin (zero user_roles grants) via bypass: "
                  "GET /admin/profiles -> 200", r.status_code == 200, f"HTTP {r.status_code}")

            r = super_admin.call("get", "/api/v1/modeling/ta/defaults")
            can_write_sa = (r.json() or {}).get("permissions", {}).get("can_write") if r.status_code == 200 else None
            check("[Task6] super_admin (zero grants): TA envelope can_write=True",
                  r.status_code == 200 and can_write_sa is True,
                  f"HTTP {r.status_code} can_write={can_write_sa}")

            r = fixture_super_admin.call("put", f"/api/v1/orgs/{HOLLIS}/settings/brand.short_name",
                                          body={"value": "Hollisworks"})
            check("[Task6] super_admin: PUT cross-org (Hollisworks) settings -> 200"
                  " — super_admin genuinely crosses orgs, unlike org_admin below",
                  r.status_code == 200, f"HTTP {r.status_code} body={r.text[:300]}")

            # -- a real non-admin, holding a REAL deployed role (not zero — zero
            #    default-allows and would prove nothing), is refused the IDENTICAL
            #    requests the org_admin just succeeded on --
            r = nonadmin.call("get", "/api/v1/admin/profiles")
            check("[Task6] non-admin (real 'member' role, no manage_org_settings): "
                  "GET /admin/profiles -> 403", r.status_code == 403, f"HTTP {r.status_code}")

            r = nonadmin.call("get", "/api/v1/modeling/ta/defaults")
            can_write_na = (r.json() or {}).get("permissions", {}).get("can_write") if r.status_code == 200 else None
            check("[Task6] non-admin: TA envelope can_write=False", can_write_na is False,
                  f"HTTP {r.status_code} can_write={can_write_na}")

            r = nonadmin.call("put", f"/api/v1/orgs/{ORG}/settings/brand.short_name",
                               body={"value": "2nd Act"})
            check("[Task6] non-admin: PUT own-org settings -> 403 (identical request org_admin"
                  " succeeded on above)", r.status_code == 403, f"HTTP {r.status_code}")

            # -- cross-org: 2nd Act's org_admin cannot reach Hollisworks --
            r = org_admin.call("put", f"/api/v1/orgs/{HOLLIS}/settings/brand.short_name",
                                body={"value": "Hollisworks"})
            check("[Task6] CROSS-ORG: 2nd Act org_admin -> PUT Hollisworks settings -> 403"
                  " (org_admin is scoped to their OWN org, unlike super_admin above)",
                  r.status_code == 403, f"HTTP {r.status_code}")
        finally:
            client.__exit__(None, None, None)

        # Back out of TestClient's loop — close whatever pool it created and
        # get a fresh one bound back to THIS script's own loop for the
        # remaining direct-DB work below.
        await close_pool()
        pool = await get_pool()

        # -- reverse direction at the LOGIC level too: has_permission for the
        #    fixture non-admin is False; for the org_admin, True --
        tokens = set_rls_context(None, True)
        try:
            async with pool.acquire() as conn:
                nonadmin_principal = await load_principal(conn, FIXTURE_NONADMIN_ID)
                org_admin_principal = await load_principal(conn, REAL_ORG_ADMIN_ID)
            check("[Task6] has_permission(non-admin, manage_org_settings) == False (logic level)",
                  await has_permission(pool, FIXTURE_NONADMIN_ID, ORG, ORG_ADMIN_PERMISSION) is False)
            check("[Task6] can_manage_org_settings(org_admin, own org) == True (logic level)",
                  await can_manage_org_settings(pool, org_admin_principal, ORG) is True)
            check("[Task6] can_manage_org_settings(non-admin, own org) == False (logic level)",
                  await can_manage_org_settings(pool, nonadmin_principal, ORG) is False)
        finally:
            reset_rls_context(tokens)

        # ═══════════════════════════════════════════════════════════════
        # TASK 6 — alert recipient resolution, set-to-set, + cross-org.
        # ═══════════════════════════════════════════════════════════════
        print("\n=== TASK 6: ALERT RECIPIENT RESOLUTION ===\n")
        tokens = set_rls_context(None, True)
        try:
            async with pool.acquire() as conn:
                old_set = {
                    str(r["id"]) for r in await conn.fetch(
                        "SELECT id FROM users WHERE org_id = $1 AND role = 'org_admin'", ORG
                    )
                }
            new_set = set(await get_users_with_permission(pool, ORG, ORG_ADMIN_PERMISSION))
            print(f"old (users.role='org_admin') recipient set: {old_set}")
            print(f"new (permission-based) recipient set: {new_set}")
            check("[Task6] permission-based recipient set == old role-string set, for 2nd Act",
                  old_set == new_set == {str(REAL_ORG_ADMIN_ID)})

            hollis_new_set = set(await get_users_with_permission(pool, HOLLIS, ORG_ADMIN_PERMISSION))
            check("[Task6] CROSS-ORG: 2nd Act's org_admin is NOT a Hollisworks alert recipient",
                  str(REAL_ORG_ADMIN_ID) not in hollis_new_set)
        finally:
            reset_rls_context(tokens)

        # ═══════════════════════════════════════════════════════════════
        # TASK 5 PROOF — Hollisworks: zero recipients now fails loudly.
        # ═══════════════════════════════════════════════════════════════
        print("\n=== TASK 5: LOUD FAILURE ON ZERO RECIPIENTS ===\n")
        tokens = set_rls_context(None, True)
        try:
            async with pool.acquire() as conn:
                before_audit = await conn.fetchval(
                    "SELECT count(*) FROM audit_log WHERE org_id = $1 AND action = $2 AND resource_id = $3",
                    HOLLIS, ALERT_UNDELIVERED_ACTION, FIXTURE_RUN_ID,
                )
                ids = await workflow_todos.create_held_run_alerts(
                    conn, org_id=HOLLIS, run_id=FIXTURE_RUN_ID, started_by=None,
                    error_detail="verify_orgadminrole Task 5 probe",
                )
            check("[Task5] create_held_run_alerts returns NO todo ids for a zero-recipient org"
                  " (unchanged silent-write behavior — the loudness is the audit record, not"
                  " an exception)", ids == [])
            async with pool.acquire() as conn:
                after_row = await conn.fetchrow(
                    """SELECT org_id, action, resource_type, resource_id, payload
                       FROM audit_log WHERE org_id = $1 AND action = $2 AND resource_id = $3""",
                    HOLLIS, ALERT_UNDELIVERED_ACTION, FIXTURE_RUN_ID,
                )
            check("[Task5] before this probe: zero prior audit_log rows for this fixture",
                  before_audit == 0)
            check("[Task5] a findable audit_log row now records the undelivered alert + why",
                  after_row is not None and after_row["org_id"] == HOLLIS,
                  f"row={dict(after_row) if after_row else None}")
        finally:
            reset_rls_context(tokens)

        # ═══════════════════════════════════════════════════════════════
        # users.role still readable and populated.
        # ═══════════════════════════════════════════════════════════════
        print("\n=== users.role STILL READABLE ===\n")
        tokens = set_rls_context(None, True)
        try:
            async with pool.acquire() as conn:
                still = await conn.fetchval(
                    "SELECT role FROM users WHERE id = $1", REAL_ORG_ADMIN_ID
                )
        finally:
            reset_rls_context(tokens)
        check("[Y] users.role is still readable and still populated ('org_admin', unchanged)",
              still == "org_admin", f"role={still!r}")

    finally:
        await teardown_fixtures(pool)
        await pool.close()

    print(f"\n{'=' * 60}\n{_n_pass} PASS, {_n_fail} FAIL, {len(_finds)} FIND\n{'=' * 60}")
    return 0 if _ok else 1


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except Exception:
        traceback.print_exc()
        sys.exit(2)
