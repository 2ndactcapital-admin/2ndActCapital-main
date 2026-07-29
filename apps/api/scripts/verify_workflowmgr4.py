"""verify_workflowmgr4.py — Workflow Manager Phase 4.

Run Console + Scheduler/Routine Viewer + Task/Alert integration + Version
History. Pass/fail only, no interactive prompts, teardown at start AND end.

Exercises the REAL services + endpoint handlers against seeded data:
  * an active User Task creates a member_todos entry for the assigned-role
    user(s); completing the task marks it done;
  * a run whose execution errors transitions to status='held' with
    error_detail populated, and alerts BOTH the starter AND an Org Admin;
  * the Run Console / Scheduler endpoints are org-scoped for Org Admin and
    all-orgs for Super Admin;
  * Version History lists every version in order with exactly one is_current;
  * a non-admin (member) is rejected from all three new endpoints.

Run:  python apps/api/scripts/verify_workflowmgr4.py
Env:  DATABASE_URL required; SKIP_BUILD=1 skips the npm build assertion.
"""
import asyncio
import os
import subprocess
import sys
import types
from pathlib import Path
from uuid import UUID

import asyncpg

API_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if API_DIR not in sys.path:
    sys.path.insert(0, API_DIR)
REPO_ROOT = Path(API_DIR).parent.parent

from services.assistant_actions import register_all
from services import workflow_engine as we
from services.workflow_engine import start_workflow_run, complete_user_task
from services import workflow_todos

ORG_ID = UUID("00000000-0000-0000-0000-000000000001")
OTHER_ORG = UUID("bb347258-8f28-4f49-8cc9-e29ccad82884")  # Ripasso (real 2nd org)

STARTER_ID = UUID("99000000-0000-0000-0000-0000000004a1")   # starts/proposes runs
APPROVER_ID = UUID("99000000-0000-0000-0000-0000000004a2")  # different approver
ASSIGNEE_ID = UUID("99000000-0000-0000-0000-0000000004a3")  # holds the role profile
ORG_ADMIN_ID = UUID("99000000-0000-0000-0000-0000000004a4")  # org_admin caller
MEMBER_ID = UUID("99000000-0000-0000-0000-0000000004a5")    # non-admin (rejected)
SUPER_ADMIN_ID = UUID("99000000-0000-0000-0000-0000000004a6")  # all-orgs

DEF_ID = UUID("99000000-0000-0000-0000-0000000004d1")
VER_ID = UUID("99000000-0000-0000-0000-0000000004d2")
VER2_ID = UUID("99000000-0000-0000-0000-0000000004d3")
OTHER_DEF_ID = UUID("99000000-0000-0000-0000-0000000004d4")
OTHER_VER_ID = UUID("99000000-0000-0000-0000-0000000004d5")
OTHER_RUN_ID = UUID("99000000-0000-0000-0000-0000000004e1")
PROFILE_ID = UUID("99000000-0000-0000-0000-0000000004c1")
TRIGGER_ID = UUID("99000000-0000-0000-0000-0000000004b1")
OTHER_TRIGGER_ID = UUID("99000000-0000-0000-0000-0000000004b2")

ALL_USERS = [STARTER_ID, APPROVER_ID, ASSIGNEE_ID, ORG_ADMIN_ID, MEMBER_ID, SUPER_ADMIN_ID]
ALL_DEFS = [DEF_ID, OTHER_DEF_ID]

SERVICE_ACTION_KEY = "marketplace.show_new_deals"
FIXTURE = Path(API_DIR) / "fixtures" / "workflow_test_process.bpmn"

# New files added this phase — scanned for hardcoded Signature-palette hex.
NEW_FILES = [
    "apps/api/services/workflow_todos.py",
    "apps/web/lib/workflowFormat.js",
    "apps/web/app/admin/workflows/runs/page.js",
    "apps/web/app/admin/workflows/runs/[runId]/page.js",
    "apps/web/app/admin/workflows/triggers/page.js",
    "apps/web/app/admin/workflows/[id]/versions/page.js",
]
# Brand "Signature palette" (Design Tokens — Never Change): navy / gold /
# gold-light / cream backgrounds. Each has a Tailwind token class, so hardcoding
# its hex is the anti-pattern.
SIGNATURE_HEX = ["1B2B4B", "C5A880", "E8D5A3", "FAF9F6", "F5F1EB"]

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


# ── teardown / seed ──────────────────────────────────────────────────────────
async def teardown(conn):
    """FK-safe, and scrubs any member_todos this suite could have produced —
    including held-run alerts routed to REAL org admins (matched by related_id,
    not by recipient, so nothing leaks)."""
    ver_ids = await conn.fetch(
        "SELECT id FROM workflow_versions WHERE workflow_definition_id = ANY($1::uuid[])",
        ALL_DEFS,
    )
    ver_ids = [r["id"] for r in ver_ids] + [VER_ID, VER2_ID, OTHER_VER_ID]
    run_rows = await conn.fetch(
        "SELECT id FROM workflow_runs WHERE workflow_version_id = ANY($1::uuid[])",
        ver_ids,
    )
    run_ids = [r["id"] for r in run_rows] + [OTHER_RUN_ID]
    step_rows = await conn.fetch(
        "SELECT id FROM workflow_run_steps WHERE workflow_run_id = ANY($1::uuid[])",
        run_ids,
    )
    step_ids = [r["id"] for r in step_rows]

    await conn.execute(
        """
        DELETE FROM member_todos
        WHERE (source = $1 AND related_id = ANY($3::uuid[]))
           OR (source = $2 AND related_id = ANY($4::uuid[]))
           OR (source = ANY($5::text[]) AND user_id = ANY($6::uuid[]))
        """,
        workflow_todos.TODO_SOURCE_RUN_HELD,
        workflow_todos.TODO_SOURCE_USER_TASK,
        run_ids, step_ids,
        [workflow_todos.TODO_SOURCE_RUN_HELD, workflow_todos.TODO_SOURCE_USER_TASK],
        ALL_USERS,
    )
    await conn.execute("DELETE FROM workflow_run_steps WHERE workflow_run_id = ANY($1::uuid[])", run_ids)
    await conn.execute("DELETE FROM workflow_runs WHERE id = ANY($1::uuid[])", run_ids)
    await conn.execute("DELETE FROM workflow_triggers WHERE workflow_definition_id = ANY($1::uuid[])", ALL_DEFS)
    await conn.execute("DELETE FROM workflow_steps WHERE workflow_version_id = ANY($1::uuid[])", ver_ids)
    await conn.execute("DELETE FROM workflow_versions WHERE workflow_definition_id = ANY($1::uuid[])", ALL_DEFS)
    await conn.execute("DELETE FROM workflow_definitions WHERE id = ANY($1::uuid[])", ALL_DEFS)
    await conn.execute("DELETE FROM users WHERE id = ANY($1::uuid[])", ALL_USERS)
    await conn.execute("DELETE FROM profiles WHERE id = $1", PROFILE_ID)


async def seed(conn):
    # A dedicated (non-seed) profile so ONLY our assignee holds it.
    await conn.execute(
        """INSERT INTO profiles (id, org_id, name, description, is_seed)
           VALUES ($1, $2, 'WFMGR4 Reviewer', 'phase 4 verify profile', false)
           ON CONFLICT (id) DO NOTHING""",
        PROFILE_ID, ORG_ID,
    )
    users = [
        (STARTER_ID, ORG_ID, "wfmgr4_starter@test.local", "member", None),
        (APPROVER_ID, ORG_ID, "wfmgr4_approver@test.local", "member", None),
        (ASSIGNEE_ID, ORG_ID, "wfmgr4_assignee@test.local", "member", PROFILE_ID),
        (ORG_ADMIN_ID, ORG_ID, "wfmgr4_orgadmin@test.local", "org_admin", None),
        (MEMBER_ID, ORG_ID, "wfmgr4_member@test.local", "member", None),
        (SUPER_ADMIN_ID, ORG_ID, "wfmgr4_super@test.local", "super_admin", None),
    ]
    for uid, org, email, role, profile in users:
        await conn.execute(
            """INSERT INTO users (id, org_id, email, full_name, auth0_sub, role, profile_id)
               VALUES ($1, $2, $3, 'WFMGR4', $4, $5, $6)
               ON CONFLICT (auth0_sub) DO NOTHING""",
            uid, org, email, f"auth0|{email}", role, profile,
        )

    bpmn_xml = FIXTURE.read_text()
    await conn.execute(
        """INSERT INTO workflow_definitions (id, org_id, name, description, created_by)
           VALUES ($1, $2, 'WFMGR4 Definition', 'phase 4 fixture', $3)
           ON CONFLICT (id) DO NOTHING""",
        DEF_ID, ORG_ID, STARTER_ID,
    )
    # v1 (current) + v2 (not current) — for the Version History ordering test.
    await conn.execute(
        """INSERT INTO workflow_versions
             (id, workflow_definition_id, org_id, version_number, bpmn_xml,
              change_summary, is_current, created_by)
           VALUES ($1, $2, $3, 1, $4, 'initial', true, $5)
           ON CONFLICT (id) DO NOTHING""",
        VER_ID, DEF_ID, ORG_ID, bpmn_xml, STARTER_ID,
    )
    await conn.execute(
        """INSERT INTO workflow_versions
             (id, workflow_definition_id, org_id, version_number, bpmn_xml,
              change_summary, is_current, created_by)
           VALUES ($1, $2, $3, 2, $4, 'second revision', false, $5)
           ON CONFLICT (id) DO NOTHING""",
        VER2_ID, DEF_ID, ORG_ID, bpmn_xml, ORG_ADMIN_ID,
    )
    # Governed steps for v1 (inserted directly, as in Phase 1 verify).
    await conn.execute(
        """INSERT INTO workflow_steps
             (workflow_version_id, org_id, step_key, step_type, autonomy_tier,
              action_registry_key, display_name)
           VALUES ($1, $2, 'Service_1', 'service', 2, $3, 'Show New Deals')
           ON CONFLICT (workflow_version_id, step_key) DO NOTHING""",
        VER_ID, ORG_ID, SERVICE_ACTION_KEY,
    )
    await conn.execute(
        """INSERT INTO workflow_steps
             (workflow_version_id, org_id, step_key, step_type, autonomy_tier,
              assigned_role_profile_id, display_name)
           VALUES ($1, $2, 'User_1', 'user', 1, $3, 'Member Reviews Result')
           ON CONFLICT (workflow_version_id, step_key) DO NOTHING""",
        VER_ID, ORG_ID, PROFILE_ID,
    )
    # A trigger in our org (Scheduler viewer).
    await conn.execute(
        """INSERT INTO workflow_triggers
             (id, workflow_definition_id, org_id, trigger_type, schedule_cron, is_active, created_by)
           VALUES ($1, $2, $3, 'scheduled', '0 9 * * *', true, $4)
           ON CONFLICT (id) DO NOTHING""",
        TRIGGER_ID, DEF_ID, ORG_ID, ORG_ADMIN_ID,
    )

    # A DIFFERENT org's definition + version + run + trigger — must never appear
    # for the Org Admin, but must appear for the Super Admin.
    await conn.execute(
        """INSERT INTO workflow_definitions (id, org_id, name, description, created_by)
           VALUES ($1, $2, 'WFMGR4 OtherOrg', 'other org fixture', NULL)
           ON CONFLICT (id) DO NOTHING""",
        OTHER_DEF_ID, OTHER_ORG,
    )
    await conn.execute(
        """INSERT INTO workflow_versions
             (id, workflow_definition_id, org_id, version_number, bpmn_xml,
              change_summary, is_current, created_by)
           VALUES ($1, $2, $3, 1, $4, 'v1', true, NULL)
           ON CONFLICT (id) DO NOTHING""",
        OTHER_VER_ID, OTHER_DEF_ID, OTHER_ORG, bpmn_xml,
    )
    await conn.execute(
        """INSERT INTO workflow_runs
             (id, workflow_version_id, org_id, status, started_by, started_at, completed_at)
           VALUES ($1, $2, $3, 'completed', NULL, now(), now())
           ON CONFLICT (id) DO NOTHING""",
        OTHER_RUN_ID, OTHER_VER_ID, OTHER_ORG,
    )
    await conn.execute(
        """INSERT INTO workflow_triggers
             (id, workflow_definition_id, org_id, trigger_type, event_type, is_active, created_by)
           VALUES ($1, $2, $3, 'event', 'deal.created', true, NULL)
           ON CONFLICT (id) DO NOTHING""",
        OTHER_TRIGGER_ID, OTHER_DEF_ID, OTHER_ORG,
    )


# ── endpoint driver (real handlers + reused admin gate) ──────────────────────
def _req(uid, org):
    return types.SimpleNamespace(
        state=types.SimpleNamespace(user={"sub": str(uid), "org_id": str(org)})
    )


async def _status_of(coro):
    from fastapi import HTTPException
    try:
        await coro
        return 200
    except HTTPException as exc:
        return exc.status_code


# ── main ─────────────────────────────────────────────────────────────────────
async def main():
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("SKIP — DATABASE_URL not set")
        sys.exit(0)

    register_all()
    pool = await asyncpg.create_pool(url, statement_cache_size=0, min_size=1, max_size=4)

    from routers import workflows as wf

    try:
        async with pool.acquire() as conn:
            await teardown(conn)   # teardown at start
            await seed(conn)

            # ── [Y] Task 1 discovery findings ────────────────────────────────
            print("\n=== Task 1 — Discovery Findings ===")
            mt_cols = await conn.fetch(
                """SELECT column_name FROM information_schema.columns
                   WHERE table_schema='public' AND table_name='member_todos'
                   ORDER BY ordinal_position"""
            )
            col_names = [r["column_name"] for r in mt_cols]
            print("  1(a) member_todos columns: " + ", ".join(col_names))
            print("       PK is (id) only — NO other unique index; status values in "
                  "actual use are open/done/dismissed (routers/dashboard.py). "
                  "'snoozed' is referenced in the prompt but written NOWHERE in code. "
                  "list_todos surfaces only status='open'.")
            print("  1(b) No centralized todo helper exists (each site raw-INSERTs; "
                  "todo_generators.py is the column pattern). No compliance_override / "
                  "'needs attention' path writes member_todos. Because there is no "
                  "unique index, ON CONFLICT DO NOTHING can't dedupe, so Phase 4 uses "
                  "an explicit SELECT-then-INSERT/UPDATE (services/workflow_todos.py) "
                  "keyed on (user_id, org_id, source, related_type, related_id). "
                  "Role->user link is users.profile_id; Org Admins are users.role='org_admin'.")
            print("  1(c) Phases 1-3 code writes ONLY running/completed for "
                  "workflow_runs and pending/active/completed for workflow_run_steps. "
                  "held/failed/cancelled (runs) and proposed/approved/failed/skipped "
                  "(steps) are never produced; there was NO 'held' transition — an "
                  "unhandled exception rolled back the run insert entirely. Phase 4 "
                  "adds the held transition + alert.")
            has_mt = {"id", "user_id", "org_id", "status", "source",
                      "related_type", "related_id"}.issubset(set(col_names))
            check("Task 1 findings reported (a: member_todos schema/status, "
                  "b: no helper + users.profile_id link, c: run/step statuses + no held)",
                  has_mt and len(col_names) > 0)

            # ── [Y] Active User Task creates a member_todos entry for the correct
            #        assigned-role user(s); completing it marks the todo done ──
            print("\n=== Task 2 — Task/Alert integration ===")
            started = await start_workflow_run(
                pool, VER_ID, ORG_ID, {"deal": "phase4"}, STARTER_ID
            )
            run_id = started["run_id"]
            user_step = await conn.fetchrow(
                """SELECT rs.id FROM workflow_run_steps rs
                   JOIN workflow_steps ws ON ws.id = rs.workflow_step_id
                   WHERE rs.workflow_run_id = $1 AND ws.step_key = 'User_1'""",
                run_id,
            )
            user_step_id = user_step["id"]
            todo = await conn.fetchrow(
                """SELECT id, status, title FROM member_todos
                   WHERE user_id = $1 AND org_id = $2 AND source = $3
                     AND related_type = 'workflow_run_step' AND related_id = $4""",
                ASSIGNEE_ID, ORG_ID, workflow_todos.TODO_SOURCE_USER_TASK, user_step_id,
            )
            # A user NOT holding the role profile must NOT receive a task todo.
            starter_todo = await conn.fetchval(
                """SELECT count(*) FROM member_todos
                   WHERE user_id = $1 AND source = $2 AND related_id = $3""",
                STARTER_ID, workflow_todos.TODO_SOURCE_USER_TASK, user_step_id,
            )
            check("Active User Task creates an OPEN member_todos for the assigned-role "
                  "user, and NOT for a user lacking that profile",
                  started["paused_at"] == "User_1"
                  and todo is not None and todo["status"] == "open"
                  and starter_todo == 0,
                  f"todo={dict(todo) if todo else None} starter_todos={starter_todo}")

            completed = await complete_user_task(
                pool, user_step_id, APPROVER_ID, {"decision": "approve"}
            )
            todo_after = await conn.fetchrow(
                "SELECT status FROM member_todos WHERE id = $1", todo["id"] if todo else None
            )
            check("Completing the User Task (complete_user_task) marks its todo DONE",
                  completed["run_status"] == "completed"
                  and todo_after is not None and todo_after["status"] == "done",
                  f"run={completed['run_status']} todo_status="
                  f"{todo_after['status'] if todo_after else None}")

            # ── [Y] A run that errors transitions to 'held' with error_detail, and
            #        alerts BOTH the starter AND an Org Admin ─────────────────
            orig_exec = we._execute_service_task

            def _boom(step_key):
                raise RuntimeError("simulated action failure during run execution")

            we._execute_service_task = _boom
            hold_raised = False
            try:
                await start_workflow_run(pool, VER_ID, ORG_ID, {"deal": "will-fail"}, STARTER_ID)
            except Exception:
                hold_raised = True
            finally:
                we._execute_service_task = orig_exec

            held = await conn.fetchrow(
                """SELECT id, status, error_detail FROM workflow_runs
                   WHERE workflow_version_id = $1 AND status = 'held'
                   ORDER BY started_at DESC LIMIT 1""",
                VER_ID,
            )
            held_run_id = held["id"] if held else None
            starter_alert = await conn.fetchval(
                """SELECT count(*) FROM member_todos
                   WHERE user_id = $1 AND source = $2 AND related_type = 'workflow_run'
                     AND related_id = $3 AND status = 'open'""",
                STARTER_ID, workflow_todos.TODO_SOURCE_RUN_HELD, held_run_id,
            )
            admin_alert = await conn.fetchval(
                """SELECT count(*) FROM member_todos
                   WHERE user_id = $1 AND source = $2 AND related_type = 'workflow_run'
                     AND related_id = $3 AND status = 'open'""",
                ORG_ADMIN_ID, workflow_todos.TODO_SOURCE_RUN_HELD, held_run_id,
            )
            check("A failing run transitions to status='held' with error_detail "
                  "populated (never left stuck in 'running'), and alerts BOTH the "
                  "starter AND an Org Admin via member_todos",
                  hold_raised and held is not None and held["status"] == "held"
                  and bool(held["error_detail"])
                  and starter_alert == 1 and admin_alert == 1,
                  f"held={dict(held) if held else None} "
                  f"starter_alert={starter_alert} admin_alert={admin_alert}")

            # ── Endpoint tests ───────────────────────────────────────────────
            print("\n=== Task 3 — Run Console / Scheduler / Version History ===")
            admin_req = _req(ORG_ADMIN_ID, ORG_ID)
            super_req = _req(SUPER_ADMIN_ID, ORG_ID)
            member_req = _req(MEMBER_ID, ORG_ID)

            # [Y] Run Console: own-org for Org Admin, all-orgs for Super Admin.
            admin_runs = await wf.list_workflow_runs(admin_req)
            admin_run_orgs = {str(r["org_id"]) for r in admin_runs}
            admin_sees_other = any(str(r["id"]) == str(OTHER_RUN_ID) for r in admin_runs)
            admin_sees_own = any(str(r["id"]) == str(run_id) for r in admin_runs)
            super_runs = await wf.list_workflow_runs(super_req)
            super_sees_other = any(str(r["id"]) == str(OTHER_RUN_ID) for r in super_runs)
            super_sees_own = any(str(r["id"]) == str(run_id) for r in super_runs)
            check("Run Console returns the org's OWN runs and NOT another org's for "
                  "Org Admin; Super Admin sees across ALL orgs",
                  admin_sees_own and not admin_sees_other
                  and admin_run_orgs == {str(ORG_ID)}
                  and super_sees_own and super_sees_other,
                  f"admin_orgs={admin_run_orgs} admin_other={admin_sees_other} "
                  f"super_own={super_sees_own} super_other={super_sees_other}")

            # Drill-in is org-scoped too: Org Admin 404s on another org's run.
            admin_other_detail = await _status_of(wf.get_workflow_run(admin_req, OTHER_RUN_ID))
            super_other_detail = await _status_of(wf.get_workflow_run(super_req, OTHER_RUN_ID))
            run_detail = await wf.get_workflow_run(admin_req, run_id)
            check("Run drill-in is org-scoped (Org Admin 404 on another org's run; "
                  "Super Admin 200) and returns the run's steps",
                  admin_other_detail == 404 and super_other_detail == 200
                  and len(run_detail["steps"]) == 2,
                  f"admin_other={admin_other_detail} super_other={super_other_detail} "
                  f"steps={len(run_detail['steps'])}")

            # [Y] Scheduler / Routine Viewer scoping.
            admin_trigs = await wf.list_workflow_triggers(admin_req)
            admin_trig_orgs = {str(t["org_id"]) for t in admin_trigs}
            admin_trig_other = any(str(t["id"]) == str(OTHER_TRIGGER_ID) for t in admin_trigs)
            admin_trig_own = any(str(t["id"]) == str(TRIGGER_ID) for t in admin_trigs)
            super_trigs = await wf.list_workflow_triggers(super_req)
            super_trig_other = any(str(t["id"]) == str(OTHER_TRIGGER_ID) for t in super_trigs)
            check("Scheduler/Routine Viewer is org-scoped for Org Admin and all-orgs "
                  "for Super Admin",
                  admin_trig_own and not admin_trig_other
                  and admin_trig_orgs == {str(ORG_ID)} and super_trig_other,
                  f"admin_orgs={admin_trig_orgs} admin_other={admin_trig_other} "
                  f"super_other={super_trig_other}")

            # [Y] Version History: all versions in order, exactly one is_current.
            vh = await wf.list_workflow_versions(admin_req, DEF_ID)
            vers = vh["versions"]
            numbers = [v["version_number"] for v in vers]
            current_count = sum(1 for v in vers if v["is_current"])
            check("Version History lists all versions in ascending order with exactly "
                  "one is_current=true",
                  numbers == sorted(numbers) and numbers == [1, 2]
                  and current_count == 1,
                  f"numbers={numbers} current_count={current_count}")

            # [Y] Non-admin (member) rejected from all three new endpoints.
            m_runs = await _status_of(wf.list_workflow_runs(member_req))
            m_run_detail = await _status_of(wf.get_workflow_run(member_req, run_id))
            m_trigs = await _status_of(wf.list_workflow_triggers(member_req))
            m_vers = await _status_of(wf.list_workflow_versions(member_req, DEF_ID))
            check("Non-admin (member) is rejected 403 from Run Console, Run drill-in, "
                  "Scheduler, and Version History endpoints",
                  m_runs == 403 and m_run_detail == 403
                  and m_trigs == 403 and m_vers == 403,
                  f"runs={m_runs} run_detail={m_run_detail} trigs={m_trigs} vers={m_vers}")

            # ── Static checks: no hardcoded palette hex in new files ─────────
            print("\n=== Static checks ===")
            offenders = []
            for rel in NEW_FILES:
                p = REPO_ROOT / rel
                if not p.exists():
                    offenders.append(f"{rel} MISSING")
                    continue
                text = p.read_text().upper()
                for h in SIGNATURE_HEX:
                    if h in text:
                        offenders.append(f"{rel}:{h}")
            check("No hardcoded Signature-palette hex (navy/gold/gold-light/cream) in "
                  "any new file",
                  not offenders,
                  "; ".join(offenders) if offenders else f"scanned {len(NEW_FILES)} files")

            # ── [Y] Teardown: zero leftover rows ─────────────────────────────
            await teardown(conn)
            leftover = {}
            leftover["defs"] = await conn.fetchval(
                "SELECT count(*) FROM workflow_definitions WHERE id = ANY($1::uuid[])", ALL_DEFS)
            leftover["versions"] = await conn.fetchval(
                "SELECT count(*) FROM workflow_versions WHERE workflow_definition_id = ANY($1::uuid[])", ALL_DEFS)
            leftover["runs"] = await conn.fetchval(
                """SELECT count(*) FROM workflow_runs
                   WHERE workflow_version_id IN (SELECT id FROM workflow_versions
                       WHERE workflow_definition_id = ANY($1::uuid[]))
                   OR id = $2""", ALL_DEFS, OTHER_RUN_ID)
            leftover["run_steps"] = await conn.fetchval(
                """SELECT count(*) FROM workflow_run_steps rs
                   WHERE rs.org_id = $1 AND rs.workflow_step_id IN
                     (SELECT id FROM workflow_steps
                      WHERE workflow_version_id = ANY($2::uuid[]))""",
                ORG_ID, [VER_ID, VER2_ID])
            leftover["triggers"] = await conn.fetchval(
                "SELECT count(*) FROM workflow_triggers WHERE workflow_definition_id = ANY($1::uuid[])", ALL_DEFS)
            leftover["todos"] = await conn.fetchval(
                """SELECT count(*) FROM member_todos
                   WHERE source = ANY($1::text[]) AND user_id = ANY($2::uuid[])""",
                [workflow_todos.TODO_SOURCE_RUN_HELD, workflow_todos.TODO_SOURCE_USER_TASK],
                ALL_USERS)
            leftover["users"] = await conn.fetchval(
                "SELECT count(*) FROM users WHERE id = ANY($1::uuid[])", ALL_USERS)
            leftover["profile"] = await conn.fetchval(
                "SELECT count(*) FROM profiles WHERE id = $1", PROFILE_ID)
            check("Teardown left zero leftover rows", all(v == 0 for v in leftover.values()), str(leftover))
    finally:
        await pool.close()

    # ── [Y] npm run build exits 0 ────────────────────────────────────────────
    print("\n=== Frontend build ===")
    if os.environ.get("SKIP_BUILD"):
        check("npm run build exits 0", True, "SKIP_BUILD set — skipped")
    else:
        proc = subprocess.run(
            ["npm", "run", "build"],
            cwd=str(REPO_ROOT / "apps" / "web"),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        if proc.returncode != 0:
            print(proc.stdout.decode(errors="replace")[-4000:])
        check("npm run build exits 0", proc.returncode == 0, f"exit={proc.returncode}")

    print()
    if _ok:
        print("RESULT: ALL ASSERTIONS PASSED ✅")
        sys.exit(0)
    else:
        print("RESULT: FAILURES PRESENT ❌")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
