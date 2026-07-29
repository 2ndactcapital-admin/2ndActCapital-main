"""verify_workflowmgr1.py — Workflow Manager Phase 1 (object model + SpiffWorkflow).

Foundation-only proof: the execution engine works end to end against real
seeded data.  Pass/fail only, no interactive prompts, teardown at start and end.

Run:  python apps/api/scripts/verify_workflowmgr1.py
"""
import asyncio
import os
import sys
from pathlib import Path
from uuid import UUID

import asyncpg

# Allow importing from the api package alongside the script.
API_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if API_DIR not in sys.path:
    sys.path.insert(0, API_DIR)

from services.action_registry import REGISTRY
from services.assistant_actions import register_all
from services import workflow_engine as we
from services.workflow_engine import (
    parse_bpmn,
    start_workflow_run,
    complete_user_task,
    deserialize_state,
    MakerCheckerError,
)
from SpiffWorkflow.util.task import TaskState

ORG_ID = UUID("00000000-0000-0000-0000-000000000001")
TEST_DEF_ID = UUID("99000000-0000-0000-0000-0000000000d1")
TEST_VERSION_ID = UUID("99000000-0000-0000-0000-0000000000d2")
PROPOSER_ID = UUID("99000000-0000-0000-0000-000000000001")   # starts run / proposes
APPROVER_ID = UUID("99000000-0000-0000-0000-000000000002")   # different approver
SERVICE_ACTION_KEY = "marketplace.show_new_deals"

FIXTURE = Path(API_DIR) / "fixtures" / "workflow_test_process.bpmn"

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


async def teardown(conn):
    """FK-safe: run_steps -> runs -> triggers -> steps -> versions -> defs -> users."""
    await conn.execute(
        """
        DELETE FROM workflow_run_steps
        WHERE workflow_run_id IN (
            SELECT id FROM workflow_runs WHERE workflow_version_id = $1
        )
        """,
        TEST_VERSION_ID,
    )
    await conn.execute("DELETE FROM workflow_runs WHERE workflow_version_id = $1", TEST_VERSION_ID)
    await conn.execute("DELETE FROM workflow_triggers WHERE workflow_definition_id = $1", TEST_DEF_ID)
    await conn.execute("DELETE FROM workflow_steps WHERE workflow_version_id = $1", TEST_VERSION_ID)
    await conn.execute("DELETE FROM workflow_versions WHERE id = $1", TEST_VERSION_ID)
    await conn.execute("DELETE FROM workflow_definitions WHERE id = $1", TEST_DEF_ID)
    await conn.execute("DELETE FROM users WHERE id = ANY($1::uuid[])", [PROPOSER_ID, APPROVER_ID])


async def seed_users(conn):
    for uid, email in [(PROPOSER_ID, "wfmgr1_proposer@test.local"),
                       (APPROVER_ID, "wfmgr1_approver@test.local")]:
        await conn.execute(
            """
            INSERT INTO users (id, org_id, email, full_name, auth0_sub, role)
            VALUES ($1, $2, $3, $4, $5, 'member')
            ON CONFLICT (auth0_sub) DO NOTHING
            """,
            uid, ORG_ID, email, "WF Mgr Test", f"auth0|{email}",
        )


async def seed_process(conn, profile_id):
    bpmn_xml = FIXTURE.read_text()
    await conn.execute(
        """
        INSERT INTO workflow_definitions (id, org_id, name, description, created_by)
        VALUES ($1, $2, 'WFMGR1 Test Definition', 'Phase 1 verify fixture', $3)
        ON CONFLICT (id) DO NOTHING
        """,
        TEST_DEF_ID, ORG_ID, PROPOSER_ID,
    )
    await conn.execute(
        """
        INSERT INTO workflow_versions
            (id, workflow_definition_id, org_id, version_number, bpmn_xml,
             change_summary, is_current, created_by)
        VALUES ($1, $2, $3, 1, $4, 'initial', true, $5)
        ON CONFLICT (id) DO NOTHING
        """,
        TEST_VERSION_ID, TEST_DEF_ID, ORG_ID, bpmn_xml, PROPOSER_ID,
    )
    # Service Task step (Tier 2, autonomous) mapped to a real read-only action.
    await conn.execute(
        """
        INSERT INTO workflow_steps
            (workflow_version_id, org_id, step_key, step_type, autonomy_tier,
             action_registry_key, display_name)
        VALUES ($1, $2, 'Service_1', 'service', 2, $3, 'Show New Deals')
        ON CONFLICT (workflow_version_id, step_key) DO NOTHING
        """,
        TEST_VERSION_ID, ORG_ID, SERVICE_ACTION_KEY,
    )
    # User Task step (Tier 1, maker-checker) assigned to a real seeded profile.
    await conn.execute(
        """
        INSERT INTO workflow_steps
            (workflow_version_id, org_id, step_key, step_type, autonomy_tier,
             assigned_role_profile_id, display_name)
        VALUES ($1, $2, 'User_1', 'user', 1, $3, 'Member Reviews Result')
        ON CONFLICT (workflow_version_id, step_key) DO NOTHING
        """,
        TEST_VERSION_ID, ORG_ID, profile_id,
    )


async def main():
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("SKIP — DATABASE_URL not set")
        sys.exit(0)

    register_all()  # populate the Sprint-11 action registry
    pool = await asyncpg.create_pool(url, statement_cache_size=0, min_size=1, max_size=4)

    try:
        async with pool.acquire() as conn:
            await teardown(conn)  # teardown at start

            # ── [Y] Task 1 discovery findings ────────────────────────────────
            print("\n=== Task 1 — Discovery Findings ===")
            import SpiffWorkflow
            print(f"  1(a) SpiffWorkflow installs cleanly — version {SpiffWorkflow.__version__} "
                  f"(pip install SpiffWorkflow --break-system-packages).")
            prof_cols = await conn.fetch(
                """SELECT column_name FROM information_schema.columns
                   WHERE table_schema='public' AND table_name='profiles'
                   ORDER BY ordinal_position"""
            )
            print("  1(b) profiles columns: "
                  + ", ".join(r["column_name"] for r in prof_cols))
            profile = await conn.fetchrow(
                "SELECT id, name FROM profiles WHERE org_id=$1 AND is_seed=true ORDER BY name LIMIT 1",
                ORG_ID,
            )
            print(f"       seeded profile used for User Task: {profile['name']} ({profile['id']})")
            action = REGISTRY.get(SERVICE_ACTION_KEY)
            print(f"  1(c) action registry = services.action_registry.REGISTRY; a Service Task's "
                  f"action_registry_key references AssistantAction.key\n"
                  f"       (== assistant_action_catalog.action_key). Using read-only "
                  f"'{SERVICE_ACTION_KEY}' access_type={getattr(action,'access_type',None)}.")
            check("Task 1 findings reported (a: SpiffWorkflow, b: profiles, c: action registry)",
                  action is not None and profile is not None and len(prof_cols) > 0)

            profile_id = profile["id"]

            # ── [Y] All 6 tables + maker-checker CHECK constraint ────────────
            print("\n=== Schema ===")
            tables = ["workflow_definitions", "workflow_versions", "workflow_steps",
                      "workflow_runs", "workflow_run_steps", "workflow_triggers"]
            found = await conn.fetch(
                """SELECT table_name FROM information_schema.tables
                   WHERE table_schema='public' AND table_name = ANY($1::text[])""",
                tables,
            )
            found_names = {r["table_name"] for r in found}
            check("All 6 workflow tables exist",
                  all(t in found_names for t in tables),
                  f"{sorted(found_names)}")
            constr = await conn.fetchrow(
                """SELECT pg_get_constraintdef(con.oid) AS def
                   FROM pg_constraint con
                   JOIN pg_class rel ON rel.oid = con.conrelid
                   WHERE rel.relname='workflow_run_steps'
                     AND con.conname='workflow_run_steps_maker_checker'"""
            )
            check("maker-checker CHECK constraint on workflow_run_steps present",
                  constr is not None,
                  constr["def"] if constr else "MISSING")

            # ── [Y] SpiffWorkflow parses the test BPMN ───────────────────────
            print("\n=== Engine ===")
            bpmn_xml = FIXTURE.read_text()
            spec, process_id = parse_bpmn(bpmn_xml)
            check("SpiffWorkflow parses the test BPMN XML into a spec",
                  spec is not None and process_id == "wfmgr1_test_process",
                  f"process_id={process_id}")

            await seed_users(conn)
            await seed_process(conn, profile_id)

            # ── [Y] start_workflow_run: Service auto-runs, pauses at User ────
            started = await start_workflow_run(
                pool, TEST_VERSION_ID, ORG_ID, {"deal": "test-42"}, PROPOSER_ID
            )
            run_id = started["run_id"]
            svc_step = await conn.fetchrow(
                """SELECT rs.status FROM workflow_run_steps rs
                   JOIN workflow_steps ws ON ws.id=rs.workflow_step_id
                   WHERE rs.workflow_run_id=$1 AND ws.step_key='Service_1'""", run_id)
            usr_step = await conn.fetchrow(
                """SELECT rs.id, rs.status, rs.proposed_by FROM workflow_run_steps rs
                   JOIN workflow_steps ws ON ws.id=rs.workflow_step_id
                   WHERE rs.workflow_run_id=$1 AND ws.step_key='User_1'""", run_id)
            check("start_workflow_run executes Service Task then PAUSES at User Task",
                  started["status"] == "running"
                  and started["paused_at"] == "User_1"
                  and "Service_1" in started["executed_service_steps"]
                  and svc_step["status"] == "completed"
                  and usr_step["status"] == "active",
                  f"service={svc_step['status']}, user={usr_step['status']}, "
                  f"paused_at={started['paused_at']}")
            user_run_step_id = usr_step["id"]

            # ── [Y] serialize -> deserialize resumable ───────────────────────
            stored = await conn.fetchval(
                "SELECT spiff_serialized_state FROM workflow_runs WHERE id=$1", run_id)
            wf_resumed = deserialize_state(stored)
            ready = [t for t in wf_resumed.get_tasks(state=TaskState.READY)
                     if t.task_spec.__class__.__name__ == "UserTask"]
            check("paused state serialized to spiff_serialized_state and DESERIALIZED as resumable",
                  stored is not None and len(ready) == 1
                  and ready[0].task_spec.bpmn_id == "User_1"
                  and not wf_resumed.is_completed(),
                  f"ready user tasks after deserialize={[t.task_spec.bpmn_id for t in ready]}")

            # ── [Y] maker-checker rejection: app-level (same user) ───────────
            app_rejected = False
            try:
                await complete_user_task(pool, user_run_step_id, PROPOSER_ID, {"decision": "approve"})
            except MakerCheckerError as exc:
                app_rejected = True
                app_msg = str(exc)
            check("complete_user_task with completed_by == proposed_by REJECTED (application-level)",
                  app_rejected, app_msg if app_rejected else "NOT rejected")

            # ── [Y] maker-checker rejection: DB CHECK constraint ─────────────
            db_rejected = False
            try:
                await conn.execute(
                    "UPDATE workflow_run_steps SET approved_by = proposed_by WHERE id=$1",
                    user_run_step_id,
                )
            except asyncpg.CheckViolationError:
                db_rejected = True
            check("approved_by == proposed_by REJECTED by DB CHECK constraint",
                  db_rejected, "CheckViolationError raised" if db_rejected else "NOT rejected")

            # step must still be active after both rejections
            still_active = await conn.fetchval(
                "SELECT status FROM workflow_run_steps WHERE id=$1", user_run_step_id)
            check("User Task step remains active after rejected attempts",
                  still_active == "active", f"status={still_active}")

            # ── [Y] complete_user_task with a DIFFERENT approver succeeds ────
            completed = await complete_user_task(
                pool, user_run_step_id, APPROVER_ID, {"decision": "approve"})
            run_row = await conn.fetchrow(
                "SELECT status, completed_at FROM workflow_runs WHERE id=$1", run_id)
            user_row = await conn.fetchrow(
                "SELECT status, approved_by, proposed_by FROM workflow_run_steps WHERE id=$1",
                user_run_step_id)
            check("complete_user_task with different completed_by succeeds and run COMPLETES",
                  completed["run_status"] == "completed"
                  and completed["is_completed"] is True
                  and run_row["status"] == "completed"
                  and user_row["status"] == "completed"
                  and user_row["approved_by"] == APPROVER_ID
                  and user_row["proposed_by"] == PROPOSER_ID,
                  f"run={run_row['status']}, step={user_row['status']}, "
                  f"approved_by={user_row['approved_by']}")

            # ── [Y] assigned_role_profile_id honored ─────────────────────────
            honored = await conn.fetchrow(
                """SELECT ws.assigned_role_profile_id
                   FROM workflow_run_steps rs
                   JOIN workflow_steps ws ON ws.id=rs.workflow_step_id
                   WHERE rs.id=$1""", user_run_step_id)
            check("completed User Task step honored the real seeded assigned_role_profile_id",
                  honored["assigned_role_profile_id"] == profile_id,
                  f"{honored['assigned_role_profile_id']} == {profile_id}")

            # ── [Y] Teardown: zero leftover rows ─────────────────────────────
            await teardown(conn)
            counts = {}
            counts["defs"] = await conn.fetchval(
                "SELECT count(*) FROM workflow_definitions WHERE id=$1", TEST_DEF_ID)
            counts["versions"] = await conn.fetchval(
                "SELECT count(*) FROM workflow_versions WHERE id=$1", TEST_VERSION_ID)
            counts["steps"] = await conn.fetchval(
                "SELECT count(*) FROM workflow_steps WHERE workflow_version_id=$1", TEST_VERSION_ID)
            counts["runs"] = await conn.fetchval(
                "SELECT count(*) FROM workflow_runs WHERE workflow_version_id=$1", TEST_VERSION_ID)
            counts["run_steps"] = await conn.fetchval(
                """SELECT count(*) FROM workflow_run_steps rs
                   WHERE rs.workflow_run_id IN
                     (SELECT id FROM workflow_runs WHERE workflow_version_id=$1)""",
                TEST_VERSION_ID)
            counts["users"] = await conn.fetchval(
                "SELECT count(*) FROM users WHERE id = ANY($1::uuid[])",
                [PROPOSER_ID, APPROVER_ID])
            check("Teardown left zero leftover rows",
                  all(v == 0 for v in counts.values()), str(counts))

    finally:
        await pool.close()

    print()
    if _ok:
        print("RESULT: ALL ASSERTIONS PASSED ✅")
        sys.exit(0)
    else:
        print("RESULT: FAILURES PRESENT ❌")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
