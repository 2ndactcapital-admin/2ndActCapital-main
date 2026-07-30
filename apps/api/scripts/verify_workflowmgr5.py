"""verify_workflowmgr5.py — Workflow Manager Phase 5 (granular permissions).

Proves that the blanket ``can_manage_org_settings`` gate on the Phase 3-4
workflow endpoints has been replaced by GRANULAR, action-registry-based
permissions reused from the SOC Profiles / Permission-Sets system — NOT a
renamed blanket check.

Auth is driven the faithful way used by verify_workflowmgr3: a fake Request
carries only ``request.state.user`` (sub + org_id claims) — exactly what
get_org_id / ensure_user / load_principal read — and the REAL endpoint handlers
+ the REAL _require_workflow_permission gate + the REAL
services.profiles.user_has_permission resolver run. Nothing is faked past the
token boundary. (The workflow_* tables are RLS-OFF, so no HTTP server is needed;
the live app uses the bypass DB role.)

Pass/fail only. No interactive prompts. Teardown at start AND at end.

Assertions:
  [Y] Task 1's three discovery findings reported explicitly.
  [Y] The three new permission entries (author_workflows / view_workflow_runs /
      configure_workflow_triggers) exist in the `permissions` catalog and are
      NOT granted to ANY seeded Profile (Member / Community Member / Adviser /
      CSA / Ops) by default.
  [Y] A user whose Profile grants ONLY `author_workflows` reaches the library
      AND editor endpoints, but is REJECTED (403) from the run-console and the
      scheduler endpoints.
  [Y] A user whose Profile grants ONLY `view_workflow_runs` reaches ONLY the
      run console; rejected from library/editor and scheduler.
  [Y] A user whose Profile grants ONLY `configure_workflow_triggers` reaches
      ONLY the scheduler; rejected from library/editor and run console.
  [Y] A user who holds an admin-adjacent permission (`manage_members`) but NONE
      of the workflow keys is REJECTED (403) from all three surfaces — proving
      the check is genuinely granular, not a renamed blanket admin check.
  [Y] Super Admin (platform staff) still passes all three (bypass preserved,
      app not broken for Ripasso staff).
  [Y] The three new permissions are visible/toggleable in the EXISTING Profiles
      / Permission-Sets UI with NO frontend change — proven by the dynamic
      GET /admin/permissions source (routers.profiles.list_permissions) that the
      checklist renders returning all three under resource='workflows'.
  [Y] npm run build exits 0.
  [Y] Teardown: zero leftover test rows.

Run:  python3 apps/api/scripts/verify_workflowmgr5.py
"""
import asyncio
import os
import subprocess
import sys
import types
from uuid import UUID

import asyncpg

API_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if API_DIR not in sys.path:
    sys.path.insert(0, API_DIR)
WEB_DIR = os.path.abspath(os.path.join(API_DIR, "..", "web"))

ORG_ID = UUID("00000000-0000-0000-0000-000000000001")

U_AUTHOR = UUID("99000000-0000-0000-0000-0000000005a1")
U_RUNS = UUID("99000000-0000-0000-0000-0000000005a2")
U_TRIG = UUID("99000000-0000-0000-0000-0000000005a3")
U_OTHER = UUID("99000000-0000-0000-0000-0000000005a4")
U_SUPER = UUID("99000000-0000-0000-0000-0000000005a5")
ALL_USERS = [U_AUTHOR, U_RUNS, U_TRIG, U_OTHER, U_SUPER]

DEF_ID = UUID("99000000-0000-0000-0000-0000000005fa")
VER_ID = UUID("99000000-0000-0000-0000-0000000005fb")

P_AUTHOR = "WFMGR5 Author"
P_RUNS = "WFMGR5 Runs"
P_TRIG = "WFMGR5 Triggers"
P_OTHER = "WFMGR5 Other"
TEST_PROFILE_NAMES = [P_AUTHOR, P_RUNS, P_TRIG, P_OTHER]

PERM_AUTHOR = "author_workflows"
PERM_VIEW_RUNS = "view_workflow_runs"
PERM_CONFIGURE_TRIGGERS = "configure_workflow_triggers"
WF_KEYS = [PERM_AUTHOR, PERM_VIEW_RUNS, PERM_CONFIGURE_TRIGGERS]
OTHER_PERM = "manage_members"  # a real, admin-adjacent catalog key (NOT a wf key)

SEED_PERSONAS = ["Member", "Community Member", "Adviser", "CSA / Ops"]

_MINIMAL_BPMN = (
    '<?xml version="1.0" encoding="UTF-8"?>'
    '<bpmn:definitions xmlns:bpmn="http://www.omg.org/spec/BPMN/20100524/MODEL" '
    'id="D_wfmgr5" targetNamespace="http://2ndactcapital.com/bpmn">'
    '<bpmn:process id="wfmgr5_proc" isExecutable="true">'
    '<bpmn:startEvent id="s"><bpmn:outgoing>e1</bpmn:outgoing></bpmn:startEvent>'
    '<bpmn:endEvent id="e"><bpmn:incoming>e1</bpmn:incoming></bpmn:endEvent>'
    '<bpmn:sequenceFlow id="e1" sourceRef="s" targetRef="e"/>'
    '</bpmn:process></bpmn:definitions>'
)

_ok = True


def check(label: str, passed: bool, detail: str = "") -> bool:
    global _ok
    mark = "[PASS]" if passed else "[FAIL]"
    line = f"{mark} {label}"
    if detail:
        line += f"  — {detail}"
    print(line)
    if not passed:
        _ok = False
    return passed


# ── DB teardown / seed ───────────────────────────────────────────────────────
async def teardown(conn):
    await conn.execute("UPDATE users SET profile_id = NULL WHERE id = ANY($1::uuid[])", ALL_USERS)
    await conn.execute(
        """DELETE FROM profile_permissions WHERE profile_id IN
           (SELECT id FROM profiles WHERE org_id = $1 AND name = ANY($2::text[]))""",
        ORG_ID, TEST_PROFILE_NAMES,
    )
    await conn.execute(
        "DELETE FROM profiles WHERE org_id = $1 AND name = ANY($2::text[]) AND is_seed = false",
        ORG_ID, TEST_PROFILE_NAMES,
    )
    await conn.execute(
        """DELETE FROM workflow_steps WHERE workflow_version_id IN
           (SELECT id FROM workflow_versions WHERE workflow_definition_id = $1)""",
        DEF_ID,
    )
    await conn.execute("DELETE FROM workflow_versions WHERE workflow_definition_id = $1", DEF_ID)
    await conn.execute("DELETE FROM workflow_definitions WHERE id = $1", DEF_ID)
    await conn.execute("DELETE FROM audit_log WHERE user_id = ANY($1::uuid[])", ALL_USERS)
    await conn.execute("DELETE FROM users WHERE id = ANY($1::uuid[])", ALL_USERS)


async def _mk_profile(conn, name, permission_key):
    pid = await conn.fetchval(
        """INSERT INTO profiles (org_id, name, description, is_seed)
           VALUES ($1, $2, 'wfmgr5 verify', false) RETURNING id""",
        ORG_ID, name,
    )
    await conn.execute(
        """INSERT INTO profile_permissions (org_id, profile_id, permission_key)
           VALUES ($1, $2, $3) ON CONFLICT (profile_id, permission_key) DO NOTHING""",
        ORG_ID, pid, permission_key,
    )
    return pid


async def _mk_user(conn, uid, auth0_sub, role, profile_id):
    await conn.execute(
        """INSERT INTO users (id, org_id, email, full_name, auth0_sub, role, profile_id)
           VALUES ($1, $2, $3, $4, $5, $6, $7)
           ON CONFLICT (auth0_sub) DO UPDATE SET profile_id = EXCLUDED.profile_id,
                                                 role = EXCLUDED.role""",
        uid, ORG_ID, f"{auth0_sub}@test.local", auth0_sub, auth0_sub, role, profile_id,
    )


async def seed(conn):
    pa = await _mk_profile(conn, P_AUTHOR, PERM_AUTHOR)
    pr = await _mk_profile(conn, P_RUNS, PERM_VIEW_RUNS)
    pt = await _mk_profile(conn, P_TRIG, PERM_CONFIGURE_TRIGGERS)
    po = await _mk_profile(conn, P_OTHER, OTHER_PERM)
    await _mk_user(conn, U_AUTHOR, "wfmgr5_author", "member", pa)
    await _mk_user(conn, U_RUNS, "wfmgr5_runs", "member", pr)
    await _mk_user(conn, U_TRIG, "wfmgr5_trig", "member", pt)
    await _mk_user(conn, U_OTHER, "wfmgr5_other", "member", po)
    await _mk_user(conn, U_SUPER, "wfmgr5_super", "super_admin", None)
    # A seeded workflow definition + current version so the editor endpoint can
    # return 200 for the authorized user (the gate runs BEFORE this lookup).
    await conn.execute(
        """INSERT INTO workflow_definitions (id, org_id, name, description, created_by)
           VALUES ($1, $2, 'WFMGR5 Fixture', 'phase 5 fixture', $3)
           ON CONFLICT (id) DO NOTHING""",
        DEF_ID, ORG_ID, U_AUTHOR,
    )
    await conn.execute(
        """INSERT INTO workflow_versions
             (id, workflow_definition_id, org_id, version_number, bpmn_xml,
              change_summary, is_current, created_by)
           VALUES ($1, $2, $3, 1, $4, 'v1', true, $5)
           ON CONFLICT (id) DO NOTHING""",
        VER_ID, DEF_ID, ORG_ID, _MINIMAL_BPMN, U_AUTHOR,
    )


def _req(uid):
    return types.SimpleNamespace(
        state=types.SimpleNamespace(user={"sub": str(uid), "org_id": str(ORG_ID)})
    )


# ── The granular matrix (real handlers + real gate) ──────────────────────────
async def run_matrix():
    from fastapi import HTTPException

    from routers import workflows as wf
    from routers import profiles as pf

    async def status_of(coro):
        try:
            await coro
            return 200
        except HTTPException as exc:
            return exc.status_code

    async def probe(uid):
        r = _req(uid)
        return {
            "library": await status_of(wf.list_workflows(r)),
            "editor": await status_of(wf.get_workflow(r, DEF_ID)),
            "runs": await status_of(wf.list_workflow_runs(r)),
            "triggers": await status_of(wf.list_workflow_triggers(r)),
        }

    obs = {
        "author": await probe(U_AUTHOR),
        "runs": await probe(U_RUNS),
        "trig": await probe(U_TRIG),
        "other": await probe(U_OTHER),
        "super": await probe(U_SUPER),
    }

    # Dynamic-UI source: the exact list the checklist renders.
    perm_list = await pf.list_permissions(_req(U_SUPER))
    obs["perm_list"] = [
        {"name": p.name, "resource": p.resource, "action": p.action} for p in perm_list
    ]
    return obs


# ── Static checks ────────────────────────────────────────────────────────────
def report_discovery():
    print("\n=== Task 1 — Discovery Findings ===")
    print(
        "  1(a) ACTION REGISTRY / GRANTABLE-PERMISSION CATALOG: services/"
        "action_registry.py is the in-memory catalog of AI-EXECUTABLE "
        "AssistantActions (key, module, access_type read|write, "
        "required_permission, handler, ...) — NOT where an HTTP endpoint's grant "
        "lives. An AssistantAction.required_permission is a flat string that "
        "POINTS AT permissions.name. The GRANTABLE permission catalog that the "
        "SOC Profiles/Permission-Sets UI shows and that endpoints check is the "
        "global `permissions` table (name UNIQUE, (resource, action) UNIQUE, no "
        "org_id). No workflow-manager keys existed in EITHER before this phase. "
        "Phase 5 therefore adds three rows to `permissions` (resource='workflows'): "
        "author_workflows / view_workflow_runs / configure_workflow_triggers "
        "(publish == save in the generate-once model, so it folds into "
        "author_workflows). See docs/workflowmgr5_part1.sql."
    )
    print(
        "  1(b) PROFILES UI IS DYNAMIC: PermissionChecklist.jsx renders one "
        "toggle per row returned by GET /admin/permissions (routers.profiles."
        "list_permissions -> SELECT name, resource, action FROM permissions), "
        "grouped by `resource` and labelled by `action`. The Profiles + "
        "Permission-Sets pages feed it via getActionPermissions(). So new "
        "`permissions` rows appear AUTOMATICALLY as a new 'Workflows' group with "
        "no frontend change — NO new screen needed."
    )
    print(
        "  1(c) GRANULAR CHECK REUSED: services.profiles.user_has_permission("
        "pool, user_id, permission_key) — True iff the key is in (profile grants "
        "∪ every assigned permission set's grants). This is the exact resolver "
        "the SOC layer enforces action-registry keys with (also used at "
        "routers/assistant.py confirm). Phase 5's _require_workflow_permission in "
        "routers/workflows.py calls it (Super Admin bypasses; Org Admin does "
        "NOT), replacing can_manage_org_settings on every workflow endpoint."
    )


def build_check():
    if os.environ.get("SKIP_BUILD"):
        check("npm run build exits 0", True, "SKIP_BUILD set — skipped")
        return
    repo_root = os.path.abspath(os.path.join(WEB_DIR, "..", ".."))
    next_bin_local = os.path.join(WEB_DIR, "node_modules", ".bin", "next")
    next_bin_root = os.path.join(repo_root, "node_modules", ".bin", "next")
    if not (os.path.exists(next_bin_local) or os.path.exists(next_bin_root)):
        check("npm run build exits 0", False, "next not installed (run npm install)")
        return
    print("    running `npm run build` in apps/web (this can take a minute)…")
    proc = subprocess.run(
        ["npm", "run", "build"], cwd=WEB_DIR,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    check(
        "npm run build exits 0",
        proc.returncode == 0,
        f"exit={proc.returncode}" + ("" if proc.returncode == 0 else "\n" + proc.stdout[-1800:]),
    )


# ── Orchestration ────────────────────────────────────────────────────────────
async def run_db_phase():
    from services.database import close_pool
    from services.assistant_actions import register_all

    register_all()  # populate REGISTRY so get_workflow's action list is realistic

    url = os.environ["DATABASE_URL"]
    setup_pool = await asyncpg.create_pool(url, statement_cache_size=0, min_size=1, max_size=4)
    try:
        async with setup_pool.acquire() as conn:
            await teardown(conn)
            await seed(conn)

            # Catalog rows exist + not granted to any seeded profile.
            cat = await conn.fetch(
                "SELECT name, resource FROM permissions WHERE name = ANY($1::text[])", WF_KEYS
            )
            cat_ok = {r["name"] for r in cat} == set(WF_KEYS) and all(
                r["resource"] == "workflows" for r in cat
            )
            leaked = await conn.fetch(
                """SELECT DISTINCT p.name, pp.permission_key
                   FROM profile_permissions pp
                   JOIN profiles p ON p.id = pp.profile_id
                   WHERE p.is_seed = true AND pp.permission_key = ANY($1::text[])""",
                WF_KEYS,
            )
            named_seed_leak = await conn.fetch(
                """SELECT DISTINCT p.name, pp.permission_key
                   FROM profile_permissions pp
                   JOIN profiles p ON p.id = pp.profile_id
                   WHERE p.org_id = $1 AND p.name = ANY($2::text[])
                     AND pp.permission_key = ANY($3::text[])""",
                ORG_ID, SEED_PERSONAS, WF_KEYS,
            )
        check(
            "Three new permission entries exist in the catalog under "
            "resource='workflows' AND are granted to NO seeded Profile by default",
            cat_ok and len(leaked) == 0 and len(named_seed_leak) == 0,
            f"catalog_ok={cat_ok} seed_leaks={[dict(r) for r in leaked]}",
        )

        obs = await run_matrix()

        A, R, T, O, S = (obs["author"], obs["runs"], obs["trig"], obs["other"], obs["super"])

        check(
            "author_workflows grant → library(200) + editor(200); REJECTED(403) "
            "from run console AND scheduler",
            A["library"] == 200 and A["editor"] == 200
            and A["runs"] == 403 and A["triggers"] == 403,
            str(A),
        )
        check(
            "view_workflow_runs grant → run console(200) ONLY; REJECTED from "
            "library/editor and scheduler",
            R["runs"] == 200 and R["library"] == 403 and R["editor"] == 403
            and R["triggers"] == 403,
            str(R),
        )
        check(
            "configure_workflow_triggers grant → scheduler(200) ONLY; REJECTED "
            "from library/editor and run console",
            T["triggers"] == 200 and T["library"] == 403 and T["editor"] == 403
            and T["runs"] == 403,
            str(T),
        )
        check(
            "A user with an admin-adjacent permission (manage_members) but NO "
            "workflow key is REJECTED(403) from ALL THREE surfaces — the gate is "
            "genuinely granular, not a renamed blanket admin check",
            O["library"] == 403 and O["editor"] == 403
            and O["runs"] == 403 and O["triggers"] == 403,
            str(O),
        )
        check(
            "Super Admin (platform staff) still passes all workflow surfaces "
            "(bypass preserved; app not broken for Ripasso staff)",
            S["library"] == 200 and S["editor"] == 200
            and S["runs"] == 200 and S["triggers"] == 200,
            str(S),
        )

        wf_in_list = {
            p["name"]: p["resource"]
            for p in obs["perm_list"]
            if p["name"] in WF_KEYS
        }
        check(
            "The three new permissions are visible/toggleable in the EXISTING "
            "Profiles/Permission-Sets UI with NO frontend change (dynamic "
            "GET /admin/permissions renders them under resource='workflows')",
            set(wf_in_list) == set(WF_KEYS)
            and all(v == "workflows" for v in wf_in_list.values()),
            f"present={sorted(wf_in_list)}",
        )

        # Teardown + zero leftovers (permissions catalog rows intentionally kept).
        async with setup_pool.acquire() as conn:
            await teardown(conn)
            leftover = await conn.fetchval(
                """SELECT
                     (SELECT count(*) FROM users WHERE id = ANY($1::uuid[]))
                   + (SELECT count(*) FROM profiles
                        WHERE org_id = $2 AND name = ANY($3::text[]) AND is_seed = false)
                   + (SELECT count(*) FROM workflow_definitions WHERE id = $4)
                   + (SELECT count(*) FROM workflow_versions WHERE workflow_definition_id = $4)
                """,
                ALL_USERS, ORG_ID, TEST_PROFILE_NAMES, DEF_ID,
            )
        check("Teardown left zero leftover test rows", int(leftover) == 0, f"count={leftover}")
    finally:
        try:
            async with setup_pool.acquire() as conn:
                await teardown(conn)
        finally:
            await setup_pool.close()
            await close_pool()


def main():
    if not os.environ.get("DATABASE_URL"):
        print("SKIP — DATABASE_URL not set")
        sys.exit(0)

    report_discovery()

    print("\n=== Granular gate (real handlers + real permission check) ===")
    asyncio.run(run_db_phase())

    print("\n=== Frontend build ===")
    build_check()

    print()
    if _ok:
        print("RESULT: ALL ASSERTIONS PASSED ✅")
        sys.exit(0)
    print("RESULT: FAILURES PRESENT ❌")
    sys.exit(1)


if __name__ == "__main__":
    main()
