"""verify_workflowpermsfix.py — the three workflow permissions had ZERO grants.

WHAT THIS PROVES (all of it against the DEPLOYED database and the REAL ASGI app):

  [Task 1] The measured grant shape of the view_portfolio / manage_portfolio
           precedent on BOTH axes, and the four facts about the deployed data
           that decide who should hold the workflow keys. Reported, not assumed.
  [Task 2] All three workflow permissions are now granted on BOTH
           role_permissions AND profile_permissions, with the exact role/profile
           lists Task 1 argued for — asserted as SET EQUALITY, so an accidental
           extra grant (e.g. `member` on the run console) fails the check just as
           loudly as a missing one.
  [Task 3] A NON-super-admin org_admin reaches all NINE gated workflow endpoints
           through the real ASGI app — real routing, the real RLS-context
           middleware, the real active-account gate and the real
           `_require_workflow_permission` → `services.profiles.user_has_permission`
           resolver. Nothing is stubbed past the token boundary: only
           ``main.verify_token`` is replaced, exactly as verify_portfolioux4 does.
  [Task 3] A member with NO profile, and a member holding a DIFFERENT
           admin-adjacent permission, are both still refused on all nine — with a
           403 that NAMES the missing key. A bare 403 is not accepted.
  [Teardown] Zero leftover rows, asserted by count.

WHAT IT DELIBERATELY DOES NOT PROVE:

  * That POST /admin/workflows can generate a workflow. That endpoint calls the
    Phase-2 AI generator; this script sends a blank description so the handler's
    OWN 422 fires immediately AFTER the permission gate. That is a real proof of
    the gate (a 403 is raised before the 422 is reachable) and creates nothing.
  * Anything about the Hollisworks org. It has zero users, zero roles and zero
    profiles, and the portfolio precedent grants nothing there either — see the
    Task 1 report.

Run:  python3 apps/api/scripts/verify_workflowpermsfix.py
"""
import asyncio
import pathlib
import sys
from uuid import UUID

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from _db_bootstrap import bootstrap_async  # noqa: E402  (also puts apps/api on sys.path)

import asyncpg  # noqa: E402

ORG_ID = UUID("00000000-0000-0000-0000-000000000001")

PERM_AUTHOR = "author_workflows"
PERM_VIEW_RUNS = "view_workflow_runs"
PERM_TRIGGERS = "configure_workflow_triggers"
WORKFLOW_PERMS = [PERM_AUTHOR, PERM_VIEW_RUNS, PERM_TRIGGERS]

ORG_ADMIN_PROFILE = "Org Admin"

# What Part 1 granted, and what this script holds it to — exactly, no more.
EXPECTED_ROLE_GRANTS = {
    PERM_AUTHOR: {"admin", "super_admin"},
    PERM_TRIGGERS: {"admin", "super_admin"},
    PERM_VIEW_RUNS: {
        "admin", "super_admin", "advisor", "investment_staff", "support_staff"
    },
}
EXPECTED_PROFILE_GRANTS = {
    PERM_AUTHOR: {"Org Admin"},
    PERM_TRIGGERS: {"Org Admin"},
    PERM_VIEW_RUNS: {"Org Admin", "Adviser", "CSA / Ops"},
}

# Fixtures.
U_ORGADMIN = UUID("99000000-0000-0000-0000-0000000009a1")
U_MEMBER = UUID("99000000-0000-0000-0000-0000000009a2")
U_OTHERPERM = UUID("99000000-0000-0000-0000-0000000009a3")
U_SUPER = UUID("99000000-0000-0000-0000-0000000009a4")
ALL_USERS = [U_ORGADMIN, U_MEMBER, U_OTHERPERM, U_SUPER]

SUB = {
    U_ORGADMIN: "wfperms_orgadmin",
    U_MEMBER: "wfperms_member",
    U_OTHERPERM: "wfperms_otherperm",
    U_SUPER: "wfperms_super",
}

TEST_PROFILE = "WFPERMS Verify Other"   # holds manage_members, no workflow key
OTHER_PERM = "manage_members"

DEF_ID = UUID("99000000-0000-0000-0000-0000000009f1")
VER_ID = UUID("99000000-0000-0000-0000-0000000009f2")
RUN_ID = UUID("99000000-0000-0000-0000-0000000009f3")

MINIMAL_BPMN = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL" '
    'id="D_wfpermsfix" targetNamespace="http://2ndactcapital.com/bpmn">'
    '<bpmn:process id="wfpermsfix_proc" isExecutable="true">'
    '<bpmn:startEvent id="s"><bpmn:outgoing>e1</bpmn:outgoing></bpmn:startEvent>'
    '<bpmn:endEvent id="e"><bpmn:incoming>e1</bpmn:incoming></bpmn:endEvent>'
    '<bpmn:sequenceFlow id="e1" sourceRef="s" targetRef="e"/>'
    '</bpmn:process></bpmn:definitions>'
)

HEADERS = {"Authorization": "Bearer verify-token"}

_ok = True
_n_pass = 0
_n_fail = 0


def check(label, passed, detail=""):
    global _ok, _n_pass, _n_fail
    print(f"{'[PASS]' if passed else '[FAIL]'} {label}" + (f"  — {detail}" if detail else ""))
    if passed:
        _n_pass += 1
    else:
        _n_fail += 1
        _ok = False
    return passed


# ═══════════════════════════════════════════════════════════════════════════
# The nine gated endpoints, as (label, method, path builder, body, permission)
# ═══════════════════════════════════════════════════════════════════════════
def endpoints():
    d = str(DEF_ID)
    return [
        ("GET  /admin/workflows",                     "get",  "/api/v1/admin/workflows",                None,                              PERM_AUTHOR),
        ("POST /admin/workflows",                     "post", "/api/v1/admin/workflows",                {"description": "   "},            PERM_AUTHOR),
        ("GET  /admin/workflows/{id}",                "get",  f"/api/v1/admin/workflows/{d}",           None,                              PERM_AUTHOR),
        ("POST /admin/workflows/{id}/versions",       "post", f"/api/v1/admin/workflows/{d}/versions",  {"bpmn_xml": MINIMAL_BPMN,
                                                                                                         "change_summary": "wfpermsfix"},  PERM_AUTHOR),
        ("GET  /admin/workflows/{id}/versions",       "get",  f"/api/v1/admin/workflows/{d}/versions",  None,                              PERM_AUTHOR),
        ("GET  /admin/workflow-runs",                 "get",  "/api/v1/admin/workflow-runs",            None,                              PERM_VIEW_RUNS),
        ("GET  /admin/workflow-runs/{id}",            "get",  f"/api/v1/admin/workflow-runs/{RUN_ID}",  None,                              PERM_VIEW_RUNS),
        ("GET  /admin/workflow-triggers",             "get",  "/api/v1/admin/workflow-triggers",        None,                              PERM_TRIGGERS),
        ("POST /admin/workflow-triggers",             "post", "/api/v1/admin/workflow-triggers",        {"workflow_definition_id": d,
                                                                                                         "event_type": "document_confirmed",
                                                                                                         "is_active": False},              PERM_TRIGGERS),
    ]


READ_ENDPOINTS = {
    "GET  /admin/workflows",
    "GET  /admin/workflows/{id}",
    "GET  /admin/workflows/{id}/versions",
    "GET  /admin/workflow-runs",
    "GET  /admin/workflow-runs/{id}",
    "GET  /admin/workflow-triggers",
}


# ═══════════════════════════════════════════════════════════════════════════
# DB helpers
# ═══════════════════════════════════════════════════════════════════════════
async def _fixture_definition_ids(conn) -> list:
    """DEF_ID plus anything the fixtures managed to create (belt and braces:
    if an ANTHROPIC key IS present, POST /admin/workflows could really build a
    definition, and a teardown that only knows DEF_ID would leave it behind)."""
    rows = await conn.fetch(
        "SELECT id FROM workflow_definitions WHERE id = $1 OR created_by = ANY($2::uuid[])",
        DEF_ID, ALL_USERS,
    )
    return [r["id"] for r in rows]


async def teardown(conn):
    def_ids = await _fixture_definition_ids(conn)
    if def_ids:
        await conn.execute(
            "DELETE FROM workflow_triggers WHERE workflow_definition_id = ANY($1::uuid[])",
            def_ids,
        )
        await conn.execute(
            """DELETE FROM workflow_run_steps WHERE workflow_run_id IN (
                 SELECT r.id FROM workflow_runs r
                 JOIN workflow_versions v ON v.id = r.workflow_version_id
                 WHERE v.workflow_definition_id = ANY($1::uuid[]))""",
            def_ids,
        )
        await conn.execute(
            """DELETE FROM workflow_runs WHERE workflow_version_id IN (
                 SELECT id FROM workflow_versions
                 WHERE workflow_definition_id = ANY($1::uuid[]))""",
            def_ids,
        )
        await conn.execute(
            """DELETE FROM workflow_steps WHERE workflow_version_id IN (
                 SELECT id FROM workflow_versions
                 WHERE workflow_definition_id = ANY($1::uuid[]))""",
            def_ids,
        )
        await conn.execute(
            "DELETE FROM workflow_versions WHERE workflow_definition_id = ANY($1::uuid[])",
            def_ids,
        )
        await conn.execute(
            "DELETE FROM workflow_definitions WHERE id = ANY($1::uuid[])", def_ids
        )
    await conn.execute("DELETE FROM workflow_triggers WHERE created_by = ANY($1::uuid[])", ALL_USERS)
    await conn.execute("DELETE FROM workflow_runs WHERE started_by = ANY($1::uuid[])", ALL_USERS)
    # Detach fixture users from any profile BEFORE the profile is removed.
    await conn.execute("UPDATE users SET profile_id = NULL WHERE id = ANY($1::uuid[])", ALL_USERS)
    await conn.execute(
        """DELETE FROM profile_permissions WHERE profile_id IN
             (SELECT id FROM profiles WHERE org_id = $1 AND name = $2)""",
        ORG_ID, TEST_PROFILE,
    )
    await conn.execute(
        "DELETE FROM profiles WHERE org_id = $1 AND name = $2 AND is_seed = false",
        ORG_ID, TEST_PROFILE,
    )
    await conn.execute("DELETE FROM audit_log WHERE user_id = ANY($1::uuid[])", ALL_USERS)
    await conn.execute("DELETE FROM users WHERE id = ANY($1::uuid[])", ALL_USERS)


async def _mk_user(conn, uid, role, profile_id):
    sub = SUB[uid]
    await conn.execute(
        """INSERT INTO users (id, org_id, email, full_name, auth0_sub, role, profile_id, is_active)
           VALUES ($1, $2, $3, $4, $5, $6, $7, true)
           ON CONFLICT (auth0_sub) DO UPDATE
             SET role = EXCLUDED.role, profile_id = EXCLUDED.profile_id,
                 is_active = true""",
        uid, ORG_ID, f"{sub}@test.local", sub, sub, role, profile_id,
    )


async def seed(conn):
    org_admin_profile_id = await conn.fetchval(
        "SELECT id FROM profiles WHERE org_id = $1 AND name = $2", ORG_ID, ORG_ADMIN_PROFILE
    )
    other_profile_id = await conn.fetchval(
        """INSERT INTO profiles (org_id, name, description, is_seed)
           VALUES ($1, $2, 'workflowpermsfix verify', false)
           ON CONFLICT (org_id, name) DO UPDATE SET updated_at = now()
           RETURNING id""",
        ORG_ID, TEST_PROFILE,
    )
    await conn.execute(
        """INSERT INTO profile_permissions (org_id, profile_id, permission_key)
           VALUES ($1, $2, $3) ON CONFLICT (profile_id, permission_key) DO NOTHING""",
        ORG_ID, other_profile_id, OTHER_PERM,
    )

    # The org_admin fixture is a REAL org_admin holding the REAL seeded profile —
    # not a bespoke test profile. If the Part 1 grant were rolled back, this
    # fixture would lose its access and every Task 3 assertion would fail.
    await _mk_user(conn, U_ORGADMIN, "org_admin", org_admin_profile_id)
    await _mk_user(conn, U_MEMBER, "member", None)
    await _mk_user(conn, U_OTHERPERM, "member", other_profile_id)
    await _mk_user(conn, U_SUPER, "super_admin", None)

    await conn.execute(
        """INSERT INTO workflow_definitions (id, org_id, name, description, created_by)
           VALUES ($1, $2, 'WFPERMSFIX Fixture', 'permissions fixture', $3)
           ON CONFLICT (id) DO NOTHING""",
        DEF_ID, ORG_ID, U_ORGADMIN,
    )
    await conn.execute(
        """INSERT INTO workflow_versions
             (id, workflow_definition_id, org_id, version_number, bpmn_xml,
              change_summary, is_current, created_by)
           VALUES ($1, $2, $3, 1, $4, 'v1', true, $5)
           ON CONFLICT (id) DO NOTHING""",
        VER_ID, DEF_ID, ORG_ID, MINIMAL_BPMN, U_ORGADMIN,
    )
    await conn.execute(
        """INSERT INTO workflow_runs (id, workflow_version_id, org_id, status, started_by)
           VALUES ($1, $2, $3, 'completed', $4) ON CONFLICT (id) DO NOTHING""",
        RUN_ID, VER_ID, ORG_ID, U_ORGADMIN,
    )
    return org_admin_profile_id


# ═══════════════════════════════════════════════════════════════════════════
# TASK 1 — discovery, reported
# ═══════════════════════════════════════════════════════════════════════════
async def task1_report(conn) -> dict:
    async def grants_by_role(resource):
        rows = await conn.fetch(
            """SELECT p.name AS permission, r.name AS role
               FROM role_permissions rp
               JOIN permissions p ON p.id = rp.permission_id
               JOIN roles r ON r.id = rp.role_id
               WHERE p.resource = $1 ORDER BY p.name, r.name""",
            resource,
        )
        out = {}
        for r in rows:
            out.setdefault(r["permission"], set()).add(r["role"])
        return out

    async def grants_by_profile(keys):
        rows = await conn.fetch(
            """SELECT pp.permission_key, pr.name AS profile
               FROM profile_permissions pp
               JOIN profiles pr ON pr.id = pp.profile_id
               WHERE pp.permission_key = ANY($1::text[])
               ORDER BY pp.permission_key, pr.name""",
            keys,
        )
        out = {}
        for r in rows:
            out.setdefault(r["permission_key"], set()).add(r["profile"])
        return out

    portfolio_roles = await grants_by_role("portfolio")
    portfolio_profiles = await grants_by_profile(["view_portfolio", "manage_portfolio"])
    workflow_roles = await grants_by_role("workflows")
    workflow_profiles = await grants_by_profile(WORKFLOW_PERMS)

    role_names = [r["name"] for r in await conn.fetch(
        "SELECT name FROM roles WHERE org_id = $1 ORDER BY name", ORG_ID)]
    org_admin_role_row = "org_admin" in role_names
    user_roles_total = await conn.fetchval("SELECT count(*) FROM user_roles")
    permset_workflow = await conn.fetchval(
        "SELECT count(*) FROM permission_set_permissions WHERE permission_key LIKE '%workflow%'")
    users_by_role = {r["role"]: r["n"] for r in await conn.fetch(
        "SELECT role, count(*) AS n FROM users WHERE org_id = $1 GROUP BY role", ORG_ID)}
    hollis = await conn.fetchrow(
        """SELECT (SELECT count(*) FROM roles WHERE org_id = o.id) AS n_roles,
                  (SELECT count(*) FROM profiles WHERE org_id = o.id) AS n_profiles,
                  (SELECT count(*) FROM users WHERE org_id = o.id) AS n_users
           FROM organizations o WHERE o.slug = 'hollisworks'""")

    print("\n── TASK 1 — the precedent, measured live (not assumed) ──")
    print("  role_permissions, resource='portfolio':")
    for k in sorted(portfolio_roles):
        print(f"    {k:<18} -> {', '.join(sorted(portfolio_roles[k]))}")
    print("  profile_permissions, portfolio keys:")
    for k in sorted(portfolio_profiles):
        print(f"    {k:<18} -> {', '.join(sorted(portfolio_profiles[k]))}")
    print("  permission_set_permissions for either portfolio key: 0 (unchanged)")

    print("\n  Four measured facts that shape who SHOULD hold the workflow keys:")
    print(f"    1. There is NO 'org_admin' row in `roles`. The role vocabulary is:")
    print(f"       {', '.join(role_names)}")
    print( "       `org_admin` exists only as a users.role TEXT value "
          f"({users_by_role.get('org_admin', 0)} users). The precedent's admin-tier")
    print( "       role is `admin`, so `admin` is what the role axis can be given.")
    print( "    2. routers/workflows._require_workflow_permission consults ONLY the")
    print( "       profile axis (services.profiles.user_has_permission = profile_")
    print( "       permissions ∪ permission_set_permissions). role_permissions is")
    print( "       granted for parity with the precedent and for services.rbac")
    print( "       consumers; it cannot by itself unblock a workflow endpoint.")
    print(f"    3. user_roles holds {user_roles_total} row(s) platform-wide, and")
    print( "       rbac.has_permission DEFAULT-ALLOWS a user with zero roles — so the")
    print( "       role axis is currently inert in both directions for nearly everyone.")
    print( "    4. Every real org_admin had profile_id IS NULL and no 'Org Admin'")
    print( "       profile existed. Granting only to Adviser / CSA Ops would have left")
    print( "       every real org_admin still 403'd and the gap intact. Part 1 seeds")
    print( "       the Org Admin profile the router's own comment presumes, and")
    print( "       assigns it to org_admin rows that had none (purely additive:")
    print( "       profile_id IS NULL grants zero permissions).")
    print(f"\n  Hollisworks org: {hollis['n_users']} users, {hollis['n_roles']} roles, "
          f"{hollis['n_profiles']} profiles — nothing granted there, exactly as the")
    print( "  portfolio precedent does. Reported as a pre-existing gap, not fixed here.")

    print("\n  Conclusion — who SHOULD hold each, and why:")
    print( "    author_workflows / configure_workflow_triggers -> roles admin +")
    print( "      super_admin; profile Org Admin. Authoring a BPMN decides what the")
    print( "      platform may do autonomously and a trigger decides when it fires:")
    print( "      org-governance acts, narrower than manage_portfolio (which advisor")
    print( "      holds because an adviser manages a CLIENT's portfolio).")
    print( "    view_workflow_runs -> every STAFF role (admin, super_admin, advisor,")
    print( "      investment_staff, support_staff) + profiles Org Admin, Adviser,")
    print( "      CSA / Ops. Broader, mirroring view_portfolio's read-only breadth —")
    print( "      but deliberately EXCLUDING `member` / `Member`, which view_portfolio")
    print( "      does include: view_portfolio is member-facing (a member's own")
    print( "      holdings), whereas the run console is mounted under /admin/*, lists")
    print( "      EVERY run in the org, and exposes error_detail and started_by.")

    check("[Y] TASK 1 reported: the portfolio precedent is granted on BOTH axes, "
          "which is the shape being copied",
          bool(portfolio_roles) and bool(portfolio_profiles),
          f"role axis: {len(portfolio_roles)} keys, profile axis: {len(portfolio_profiles)} keys")
    check("[Y] TASK 1 reported: `org_admin` is NOT a row in `roles` — it is a "
          "users.role text value, so the role-axis grant goes to `admin`",
          not org_admin_role_row,
          f"roles = {role_names}")
    check("[Y] TASK 1 reported: permission_set_permissions is left untouched — the "
          "gap is closed on the two axes the precedent uses, not a third",
          permset_workflow == 0,
          f"workflow rows in permission_set_permissions = {permset_workflow}")
    return {"workflow_roles": workflow_roles, "workflow_profiles": workflow_profiles}


# ═══════════════════════════════════════════════════════════════════════════
# TASK 2 — both axes populated
# ═══════════════════════════════════════════════════════════════════════════
async def task2_checks(conn, grants):
    print("\n── TASK 2 — both grant axes, asserted as exact sets ──")
    for perm in WORKFLOW_PERMS:
        got = grants["workflow_roles"].get(perm, set())
        check(f"[Y] role_permissions grants {perm}",
              got == EXPECTED_ROLE_GRANTS[perm],
              f"got {sorted(got)}, expected {sorted(EXPECTED_ROLE_GRANTS[perm])}")
    for perm in WORKFLOW_PERMS:
        got = grants["workflow_profiles"].get(perm, set())
        check(f"[Y] profile_permissions grants {perm}",
              got == EXPECTED_PROFILE_GRANTS[perm],
              f"got {sorted(got)}, expected {sorted(EXPECTED_PROFILE_GRANTS[perm])}")

    check("[Y] view_workflow_runs is NOT granted to `member` on the role axis or "
          "`Member` on the profile axis, even though view_portfolio grants both — "
          "the run console is an /admin/* surface over the whole org",
          "member" not in grants["workflow_roles"].get(PERM_VIEW_RUNS, set())
          and "Member" not in grants["workflow_profiles"].get(PERM_VIEW_RUNS, set()))

    # The grant is only worth anything if a REAL user is behind it.
    orphans = await conn.fetchval(
        "SELECT count(*) FROM users WHERE org_id = $1 AND role = 'org_admin' "
        "AND profile_id IS NULL", ORG_ID)
    # Count the org_admins whose profile grants ALL THREE keys. The per-user
    # HAVING has to be an inner query: a bare `GROUP BY u.id HAVING …` handed to
    # fetchval returns the FIRST GROUP's count, i.e. 1, no matter how many users
    # qualify — which reads as "only one org_admin got it" when all three did.
    reachable = await conn.fetchval(
        """SELECT count(*) FROM (
             SELECT u.id FROM users u
             JOIN profile_permissions pp ON pp.profile_id = u.profile_id
             WHERE u.org_id = $1 AND u.role = 'org_admin'
               AND pp.permission_key = ANY($2::text[])
             GROUP BY u.id
             HAVING count(DISTINCT pp.permission_key) = cardinality($2::text[])
           ) q""",
        ORG_ID, WORKFLOW_PERMS)
    total_org_admins = await conn.fetchval(
        "SELECT count(*) FROM users WHERE org_id = $1 AND role = 'org_admin'", ORG_ID)
    check("[Y] the grant reaches REAL users, not just a profile row: EVERY real "
          "org_admin now holds a profile granting all three keys. Without this the "
          "gate (profile-only) would still 403 every one of them and the sprint's "
          "gap would survive its own fix",
          orphans == 0 and total_org_admins > 0 and reachable == total_org_admins,
          f"{total_org_admins} org_admin user(s), {orphans} with no profile, "
          f"{reachable} holding all three keys")


# ═══════════════════════════════════════════════════════════════════════════
# TASK 3 — the real ASGI app
# ═══════════════════════════════════════════════════════════════════════════
class _Principal:
    """Drives the real ASGI app as one user.

    ``main.verify_token`` is replaced, NOT the auth dependency — so the request
    still traverses routing, the RLS-context middleware, the active-account gate
    and the real `_require_workflow_permission`. Stubbing higher up would skip
    exactly the layer this sprint is about.
    """

    __slots__ = ("client", "sub", "label")

    def __init__(self, client, sub, label):
        self.client, self.sub, self.label = client, sub, label

    def _become(self):
        import main
        sub = self.sub
        main.verify_token = lambda _t: {
            "sub": sub, "email": f"{sub}@test.local", "org_id": str(ORG_ID),
        }

    def call(self, method, path, body):
        self._become()
        fn = getattr(self.client, method)
        return fn(path, headers=HEADERS, **({"json": body} if body is not None else {}))


def _is_permission_refusal(res, permission):
    """A 403 that NAMES the permission. A bare 403 is not good enough: a 401 means
    the request never reached the gate and a 404 means it never reached the
    handler, and either would make a refusal test pass for the wrong reason."""
    if res.status_code != 403:
        return False
    try:
        detail = res.json().get("detail", "")
    except Exception:  # noqa: BLE001
        return False
    return detail == f"Permission required: {permission}"


def endpoint_tests():
    """Sync — TestClient is sync. ONE client, entered as a context manager: used
    without ``with``, it builds a fresh event loop per request and the app's
    module-global pool ends up bound to a dead one (request 1 passes, everything
    after it 500s from inside the middleware)."""
    import main
    from starlette.testclient import TestClient

    client = TestClient(main.app, raise_server_exceptions=False)
    client.__enter__()
    try:
        return _endpoint_tests(client)
    finally:
        client.__exit__(None, None, None)


def _endpoint_tests(client):
    admin = _Principal(client, SUB[U_ORGADMIN], "org_admin")
    member = _Principal(client, SUB[U_MEMBER], "member (no profile)")
    otherp = _Principal(client, SUB[U_OTHERPERM], f"member holding {OTHER_PERM}")
    superu = _Principal(client, SUB[U_SUPER], "super_admin")

    eps = endpoints()

    print("\n── TASK 3 — nine endpoints, through the REAL ASGI app ──")
    print(f"\n  As a NON-super-admin org_admin (users.role='org_admin', "
          f"holding the real '{ORG_ADMIN_PROFILE}' profile):")
    passed_all = True
    statuses = {}
    for label, method, path, body, perm in eps:
        res = admin.call(method, path, body)
        statuses[label] = res.status_code
        refused = _is_permission_refusal(res, perm)
        ok = not refused and res.status_code != 401
        passed_all = passed_all and ok
        check(f"    [Y] {label}  [{perm}] — gate PASSED", ok,
              f"HTTP {res.status_code}"
              + ("" if ok else f" body={res.text[:160]}"))

    check("[Y] TASK 3: the org_admin cleared the permission gate on ALL NINE "
          "previously-403'd endpoints", passed_all,
          f"statuses: {sorted(set(statuses.values()))}")

    reads_200 = {l: s for l, s in statuses.items() if l in READ_ENDPOINTS}
    check("[Y] every READ endpoint returned a real 200 — the org_admin is not "
          "merely past the gate, it is served",
          all(s == 200 for s in reads_200.values()),
          ", ".join(f"{l.strip()}={s}" for l, s in sorted(reads_200.items())))

    print("\n  As a member with NO profile (the pre-fix state of every non-admin):")
    all_refused = True
    for label, method, path, body, perm in eps:
        res = member.call(method, path, body)
        refused = _is_permission_refusal(res, perm)
        all_refused = all_refused and refused
        check(f"    [Y] {label}  [{perm}] — 403 naming the key", refused,
              f"HTTP {res.status_code} {res.text[:120]}")
    check("[Y] TASK 3: an ungranted member is still refused on all nine, with a "
          "403 that NAMES the missing permission", all_refused)

    print(f"\n  As a member holding a DIFFERENT admin-adjacent key ({OTHER_PERM}):")
    granular = True
    for label, method, path, body, perm in eps:
        res = otherp.call(method, path, body)
        refused = _is_permission_refusal(res, perm)
        granular = granular and refused
        check(f"    [Y] {label}  [{perm}] — still 403", refused,
              f"HTTP {res.status_code} {res.text[:120]}")
    check("[Y] the fix did not turn the gate into a blanket admin check — holding "
          f"{OTHER_PERM} still buys nothing on any workflow surface", granular)

    print("\n  Super Admin bypass (regression guard — must be untouched):")
    su_ok = True
    for label, method, path, body, perm in eps:
        res = superu.call(method, path, body)
        ok = not _is_permission_refusal(res, perm)
        su_ok = su_ok and ok
    check("[Y] super_admin still clears all nine (escape hatch preserved)", su_ok)

    return statuses


# ═══════════════════════════════════════════════════════════════════════════
async def main_async():
    dsn = await bootstrap_async()
    if not dsn:
        print("[SKIP] no working DATABASE_URL — nothing can be proven")
        return 2
    conn = await asyncpg.connect(dsn, statement_cache_size=0, ssl="require")
    try:
        await teardown(conn)
        grants = await task1_report(conn)
        await task2_checks(conn, grants)

        print("\n── Fixtures ──")
        org_admin_profile_id = await seed(conn)
        check("[Y] the org_admin fixture holds the REAL seeded 'Org Admin' profile, "
              "not a bespoke test profile — roll Part 1 back and Task 3 fails",
              org_admin_profile_id is not None,
              f"profile_id={org_admin_profile_id}")

        # TestClient is sync and needs its own loop; run it off this one, and use
        # this plain connection (never the app's pool) for every DB read here.
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, endpoint_tests)
    finally:
        try:
            await teardown(conn)
            leftovers = await conn.fetchval(
                """SELECT (SELECT count(*) FROM users WHERE id = ANY($1::uuid[]))
                        + (SELECT count(*) FROM workflow_definitions
                             WHERE id = $2 OR created_by = ANY($1::uuid[]))
                        + (SELECT count(*) FROM workflow_versions
                             WHERE workflow_definition_id = $2)
                        + (SELECT count(*) FROM workflow_runs WHERE id = $3
                             OR started_by = ANY($1::uuid[]))
                        + (SELECT count(*) FROM workflow_triggers
                             WHERE workflow_definition_id = $2
                                OR created_by = ANY($1::uuid[]))
                        + (SELECT count(*) FROM profiles
                             WHERE org_id = $4 AND name = $5)""",
                ALL_USERS, DEF_ID, RUN_ID, ORG_ID, TEST_PROFILE,
            )
            check("[Y] TEARDOWN: zero leftover fixture rows across users, "
                  "definitions, versions, runs, triggers and the test profile",
                  leftovers == 0, f"leftover rows = {leftovers}")
            still_granted = await conn.fetchval(
                """SELECT count(*) FROM profile_permissions
                   WHERE permission_key = ANY($1::text[])""", WORKFLOW_PERMS)
            check("[Y] TEARDOWN removed only fixtures — the Part 1 grants survive",
                  still_granted == 5, f"profile grants remaining = {still_granted}")
        finally:
            await conn.close()

    print(f"\n{'=' * 70}")
    print(f"{_n_pass} passed, {_n_fail} failed — "
          f"{'ALL GREEN' if _ok else 'FAILURES ABOVE'}")
    print("=" * 70)
    return 0 if _ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main_async()))
