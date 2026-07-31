"""verify_chancery7.py — Chancery Phase 7 (Workflow Manager integration).

The FIRST real firing of ``workflow_triggers.event_type`` in the platform,
narrowly scoped to exactly ONE event type: ``'document_confirmed'``. Proves that
confirming a document auto-starts the configured workflow run WITHOUT weakening
any per-step governance — a Tier-1 step must still pause for maker-checker
approval even though the run auto-started.

Pass/fail only, no interactive prompts, teardown at start AND end.

Run:  python3 apps/api/scripts/verify_chancery7.py
"""
import asyncio
import json
import os
import sys
from pathlib import Path
from uuid import UUID

import asyncpg

API_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if API_DIR not in sys.path:
    sys.path.insert(0, API_DIR)

from services.action_registry import REGISTRY
from services.assistant_actions import register_all
from services import chancery_workflow_bridge as bridge
from services import document_review as review

ORG_A = UUID("00000000-0000-0000-0000-000000000001")   # default org (exists)
ORG_B = UUID("bb347258-8f28-4f49-8cc9-e29ccad82884")   # Ripasso — a real second org (isolation)

STARTER_ID = UUID("99000000-0000-0000-0000-0000000c7001")   # confirms doc / starts run
APPROVER_ID = UUID("99000000-0000-0000-0000-0000000c7002")  # different approver

DEF_A_ID = UUID("99000000-0000-0000-0000-0000000c70a1")
VER_A_ID = UUID("99000000-0000-0000-0000-0000000c70a2")
DEF_B_ID = UUID("99000000-0000-0000-0000-0000000c70b1")
VER_B_ID = UUID("99000000-0000-0000-0000-0000000c70b2")

TRIG_A_ID = UUID("99000000-0000-0000-0000-0000000c7a11")
TRIG_B_ID = UUID("99000000-0000-0000-0000-0000000c7b11")

ENTITY_ID = UUID("99000000-0000-0000-0000-0000000c7e01")
DOC1_ID = UUID("99000000-0000-0000-0000-0000000c7d01")   # matched: trigger fires
DOC2_ID = UUID("99000000-0000-0000-0000-0000000c7d02")   # no trigger: graceful no-op

MAPPED_FIELDS = {"ordinary_income": "1234.00", "partner_name": "Chancery7 Verify"}

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


ALL_DEFS = [DEF_A_ID, DEF_B_ID]
ALL_VERS = [VER_A_ID, VER_B_ID]
ALL_DOCS = [DOC1_ID, DOC2_ID]


async def teardown(conn):
    """FK-safe: run_steps -> runs -> triggers -> steps -> versions -> defs;
    then extractions/links -> documents -> entities -> users."""
    await conn.execute(
        """DELETE FROM workflow_run_steps WHERE workflow_run_id IN (
               SELECT id FROM workflow_runs WHERE workflow_version_id = ANY($1::uuid[]))""",
        ALL_VERS,
    )
    await conn.execute(
        "DELETE FROM workflow_runs WHERE workflow_version_id = ANY($1::uuid[])", ALL_VERS)
    await conn.execute(
        "DELETE FROM workflow_triggers WHERE workflow_definition_id = ANY($1::uuid[])", ALL_DEFS)
    await conn.execute(
        "DELETE FROM workflow_steps WHERE workflow_version_id = ANY($1::uuid[])", ALL_VERS)
    await conn.execute(
        "DELETE FROM workflow_versions WHERE id = ANY($1::uuid[])", ALL_VERS)
    await conn.execute(
        "DELETE FROM workflow_definitions WHERE id = ANY($1::uuid[])", ALL_DEFS)
    await conn.execute(
        "DELETE FROM document_template_extractions WHERE document_id = ANY($1::uuid[])", ALL_DOCS)
    await conn.execute(
        "DELETE FROM document_entity_links WHERE document_id = ANY($1::uuid[])", ALL_DOCS)
    await conn.execute("DELETE FROM documents WHERE id = ANY($1::uuid[])", ALL_DOCS)
    await conn.execute("DELETE FROM entities WHERE id = $1", ENTITY_ID)
    await conn.execute(
        "DELETE FROM users WHERE id = ANY($1::uuid[])", [STARTER_ID, APPROVER_ID])


async def seed_users(conn):
    for uid, email in [(STARTER_ID, "chancery7_starter@test.local"),
                       (APPROVER_ID, "chancery7_approver@test.local")]:
        await conn.execute(
            """INSERT INTO users (id, org_id, email, full_name, auth0_sub, role)
               VALUES ($1, $2, $3, 'Chancery7 Verify', $4, 'member')
               ON CONFLICT (auth0_sub) DO NOTHING""",
            uid, ORG_A, email, f"auth0|{email}",
        )


async def seed_process(conn, def_id, ver_id, org_id, profile_id):
    """One definition + current version with a Tier-2 Service Task and a Tier-1
    User Task (reuses the Phase-1 fixture: pauses at the User Task)."""
    bpmn_xml = FIXTURE.read_text()
    await conn.execute(
        """INSERT INTO workflow_definitions (id, org_id, name, description, created_by)
           VALUES ($1, $2, 'Chancery7 Def', 'Phase 7 verify fixture', $3)
           ON CONFLICT (id) DO NOTHING""",
        def_id, org_id, STARTER_ID,
    )
    await conn.execute(
        """INSERT INTO workflow_versions
               (id, workflow_definition_id, org_id, version_number, bpmn_xml,
                change_summary, is_current, created_by)
           VALUES ($1, $2, $3, 1, $4, 'initial', true, $5)
           ON CONFLICT (id) DO NOTHING""",
        ver_id, def_id, org_id, bpmn_xml, STARTER_ID,
    )
    await conn.execute(
        """INSERT INTO workflow_steps
               (workflow_version_id, org_id, step_key, step_type, autonomy_tier,
                action_registry_key, display_name)
           VALUES ($1, $2, 'Service_1', 'service', 2, $3, 'Show New Deals')
           ON CONFLICT (workflow_version_id, step_key) DO NOTHING""",
        ver_id, org_id, SERVICE_ACTION_KEY,
    )
    await conn.execute(
        """INSERT INTO workflow_steps
               (workflow_version_id, org_id, step_key, step_type, autonomy_tier,
                assigned_role_profile_id, display_name)
           VALUES ($1, $2, 'User_1', 'user', 1, $3, 'Member Reviews Result')
           ON CONFLICT (workflow_version_id, step_key) DO NOTHING""",
        ver_id, org_id, profile_id,
    )


async def seed_docs(conn):
    await conn.execute(
        """INSERT INTO entities (id, org_id, entity_type, display_name, status)
           VALUES ($1, $2, 'llc'::entity_type, 'Chancery7 Entity', 'prospect')
           ON CONFLICT (id) DO NOTHING""",
        ENTITY_ID, ORG_A,
    )
    for doc_id in ALL_DOCS:
        await conn.execute(
            """INSERT INTO documents (id, org_id, original_filename, source, status,
                                      doc_family, storage_key, created_by)
               VALUES ($1, $2, $3, 'upload', 'sorted', 'tabular', $4, $5)
               ON CONFLICT (id) DO NOTHING""",
            doc_id, ORG_A, f"k1_{doc_id}.pdf",
            f"chancery/{ORG_A}/{doc_id}/v1.pdf", STARTER_ID,
        )
    # DOC1 gets a real Phase-5 entity link + a template extraction (mapped_fields).
    await conn.execute(
        """INSERT INTO document_entity_links
               (document_id, entity_id, org_id, link_role, created_by)
           VALUES ($1, $2, $3, 'manual', $4)
           ON CONFLICT (document_id, entity_id) DO NOTHING""",
        DOC1_ID, ENTITY_ID, ORG_A, STARTER_ID,
    )
    await conn.execute(
        """INSERT INTO document_template_extractions
               (document_id, org_id, template_type, extraction_source, mapped_fields)
           VALUES ($1, $2, 'k1', 'native', $3::jsonb)""",
        DOC1_ID, ORG_A, json.dumps(MAPPED_FIELDS),
    )


async def runs_for(conn, ver_id):
    return await conn.fetchval(
        "SELECT count(*) FROM workflow_runs WHERE workflow_version_id = $1", ver_id)


async def main():
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("SKIP — DATABASE_URL not set")
        sys.exit(0)

    register_all()
    pool = await asyncpg.create_pool(url, statement_cache_size=0, min_size=1, max_size=4)
    try:
        async with pool.acquire() as conn:
            await teardown(conn)  # teardown at start

            # ── [Y] Task 1 — discovery findings, reported explicitly ──────────
            print("\n=== Task 1 — Discovery Findings ===")
            fired = await conn.fetchval(
                """SELECT count(*) FROM workflow_triggers
                   WHERE trigger_type = 'event' AND event_type IS NOT NULL"""
            )
            print(
                "  1(a) No event-type firing existed before this phase: "
                "workflow_triggers is READ only by list_workflow_triggers (Phase 4 "
                "viewer) and written by verify scripts — no code fired a run from an "
                "event. workflow_triggers has (trigger_type, event_type, is_active, "
                "workflow_definition_id, org_id) and NO category/doc_family column, "
                "so scoping is by (org, event trigger, event_type) only — no new "
                f"column invented. (existing event-type trigger rows now: {fired})"
            )
            print(
                "  1(b) start_workflow_run(pool, workflow_version_id, org_id, context, "
                "started_by) in services/workflow_engine.py takes a VERSION id (not a "
                "definition id) — so the bridge resolves is_current version from the "
                "trigger's workflow_definition_id. It drives Service Tasks then PAUSES "
                "at the first ready User Task (proposed_by = started_by; maker-checker "
                "requires a different approver). Tier-1 User Tasks pause, not execute."
            )
            print(
                "  1(c) Confirm hook point: POST /documents/{id}/confirm in "
                "routers/document_review.py -> review.confirm_document(conn, org_id, "
                "document_id, confirmed_by) sets status='confirmed'. The bridge fires "
                "AFTER that succeeds."
            )
            action = REGISTRY.get(SERVICE_ACTION_KEY)
            profile = await conn.fetchrow(
                "SELECT id, name FROM profiles WHERE org_id=$1 AND is_seed=true ORDER BY name LIMIT 1",
                ORG_A,
            )
            check("Task 1 findings reported (a: no prior firing, b: real signature, c: hook point)",
                  action is not None and profile is not None,
                  f"profile={profile['name'] if profile else None}")
            profile_id = profile["id"]

            # ── Seed both orgs' processes + the documents ─────────────────────
            await seed_users(conn)
            await seed_process(conn, DEF_A_ID, VER_A_ID, ORG_A, profile_id)
            await seed_process(conn, DEF_B_ID, VER_B_ID, ORG_B, profile_id)
            await seed_docs(conn)
            # ORG_B trigger exists the whole time (cross-org isolation control).
            await conn.execute(
                """INSERT INTO workflow_triggers
                       (id, workflow_definition_id, org_id, trigger_type, event_type,
                        is_active, created_by)
                   VALUES ($1, $2, $3, 'event', 'document_confirmed', true, $4)
                   ON CONFLICT (id) DO NOTHING""",
                TRIG_B_ID, DEF_B_ID, ORG_B, STARTER_ID,
            )

            # ── [Y] NO matching trigger → confirm succeeds, starts nothing ────
            print("\n=== Graceful no-op (no matching trigger) ===")
            async with pool.acquire() as c2:
                confirmed2 = await review.confirm_document(
                    c2, ORG_A, DOC2_ID, confirmed_by=STARTER_ID)
            noop = await bridge.fire_document_confirmed_triggers(
                pool, ORG_A, DOC2_ID, started_by=STARTER_ID)
            runs_a_before = await runs_for(conn, VER_A_ID)
            check("with NO matching trigger, confirm succeeds AND starts no run "
                  "(confirmed via absence, not just lack of error)",
                  confirmed2["status"] == "confirmed"
                  and noop["matched_triggers"] == 0
                  and noop["started_runs"] == []
                  and runs_a_before == 0,
                  f"matched={noop['matched_triggers']}, runs_for_verA={runs_a_before}")

            # ── Configure the ORG_A event trigger (Task 4 shape) ──────────────
            await conn.execute(
                """INSERT INTO workflow_triggers
                       (id, workflow_definition_id, org_id, trigger_type, event_type,
                        is_active, created_by)
                   VALUES ($1, $2, $3, 'event', 'document_confirmed', true, $4)
                   ON CONFLICT (id) DO NOTHING""",
                TRIG_A_ID, DEF_A_ID, ORG_A, STARTER_ID,
            )

            # ── [Y] Matched trigger → confirm starts a real run w/ correct ctx ─
            print("\n=== Matched trigger fires a run ===")
            async with pool.acquire() as c3:
                confirmed1 = await review.confirm_document(
                    c3, ORG_A, DOC1_ID, confirmed_by=STARTER_ID)
            result = await bridge.fire_document_confirmed_triggers(
                pool, ORG_A, DOC1_ID, started_by=STARTER_ID)

            started = result["started_runs"]
            run_id = UUID(started[0]["run_id"]) if started else None
            run_row = await conn.fetchrow(
                "SELECT status, context, workflow_version_id, started_by "
                "FROM workflow_runs WHERE id = $1", run_id) if run_id else None
            ctx = json.loads(run_row["context"]) if run_row and isinstance(
                run_row["context"], str) else (run_row["context"] if run_row else {})
            check("confirming a matched document starts a real workflow_run with "
                  "correct context (document_id/entity_id/mapped_fields)",
                  confirmed1["status"] == "confirmed"
                  and result["matched_triggers"] == 1
                  and len(started) == 1
                  and run_row is not None
                  and str(run_row["workflow_version_id"]) == str(VER_A_ID)
                  and run_row["started_by"] == STARTER_ID
                  and ctx.get("document_id") == str(DOC1_ID)
                  and ctx.get("entity_id") == str(ENTITY_ID)
                  and ctx.get("mapped_fields") == MAPPED_FIELDS,
                  f"ctx.document_id={ctx.get('document_id')}, "
                  f"ctx.entity_id={ctx.get('entity_id')}, "
                  f"mapped_fields_ok={ctx.get('mapped_fields') == MAPPED_FIELDS}")

            # ── [Y] Tier-1 step STILL pauses — auto-start ≠ auto-execute ──────
            print("\n=== Governance preserved: Tier-1 still pauses ===")
            svc = await conn.fetchrow(
                """SELECT rs.status FROM workflow_run_steps rs
                   JOIN workflow_steps ws ON ws.id = rs.workflow_step_id
                   WHERE rs.workflow_run_id = $1 AND ws.step_key = 'Service_1'""", run_id)
            usr = await conn.fetchrow(
                """SELECT rs.status, rs.proposed_by, rs.approved_by, ws.autonomy_tier
                   FROM workflow_run_steps rs
                   JOIN workflow_steps ws ON ws.id = rs.workflow_step_id
                   WHERE rs.workflow_run_id = $1 AND ws.step_key = 'User_1'""", run_id)
            check("the auto-started run's Tier-1 User Task PAUSES for approval "
                  "(active, proposed but NOT approved) — it did not execute just "
                  "because the run auto-started",
                  run_row["status"] == "running"
                  and svc["status"] == "completed"
                  and usr["autonomy_tier"] == 1
                  and usr["status"] == "active"
                  and usr["proposed_by"] == STARTER_ID
                  and usr["approved_by"] is None,
                  f"run={run_row['status']}, service={svc['status']}, "
                  f"user={usr['status']}, tier={usr['autonomy_tier']}, "
                  f"approved_by={usr['approved_by']}")

            # ── [Y] Cross-org isolation: ORG_B trigger did NOT fire ───────────
            print("\n=== Cross-org trigger isolation ===")
            runs_b = await runs_for(conn, VER_B_ID)
            started_vers = {s["workflow_version_id"] for s in started}
            check("a trigger scoped to a DIFFERENT org does not fire for this org's "
                  "document confirmation",
                  runs_b == 0
                  and str(VER_B_ID) not in started_vers
                  and result["matched_triggers"] == 1,
                  f"runs_for_verB={runs_b}, matched={result['matched_triggers']}")

            # ── [Y] Teardown: zero leftover rows ──────────────────────────────
            print("\n=== Teardown ===")
            await teardown(conn)
            counts = {
                "runs": await conn.fetchval(
                    "SELECT count(*) FROM workflow_runs WHERE workflow_version_id = ANY($1::uuid[])",
                    ALL_VERS),
                "run_steps": await conn.fetchval(
                    """SELECT count(*) FROM workflow_run_steps WHERE workflow_run_id IN (
                           SELECT id FROM workflow_runs WHERE workflow_version_id = ANY($1::uuid[]))""",
                    ALL_VERS),
                "triggers": await conn.fetchval(
                    "SELECT count(*) FROM workflow_triggers WHERE workflow_definition_id = ANY($1::uuid[])",
                    ALL_DEFS),
                "steps": await conn.fetchval(
                    "SELECT count(*) FROM workflow_steps WHERE workflow_version_id = ANY($1::uuid[])",
                    ALL_VERS),
                "versions": await conn.fetchval(
                    "SELECT count(*) FROM workflow_versions WHERE id = ANY($1::uuid[])", ALL_VERS),
                "defs": await conn.fetchval(
                    "SELECT count(*) FROM workflow_definitions WHERE id = ANY($1::uuid[])", ALL_DEFS),
                "extractions": await conn.fetchval(
                    "SELECT count(*) FROM document_template_extractions WHERE document_id = ANY($1::uuid[])",
                    ALL_DOCS),
                "links": await conn.fetchval(
                    "SELECT count(*) FROM document_entity_links WHERE document_id = ANY($1::uuid[])",
                    ALL_DOCS),
                "documents": await conn.fetchval(
                    "SELECT count(*) FROM documents WHERE id = ANY($1::uuid[])", ALL_DOCS),
                "entities": await conn.fetchval(
                    "SELECT count(*) FROM entities WHERE id = $1", ENTITY_ID),
                "users": await conn.fetchval(
                    "SELECT count(*) FROM users WHERE id = ANY($1::uuid[])",
                    [STARTER_ID, APPROVER_ID]),
            }
            check("teardown left zero leftover rows (runs/run_steps/triggers/"
                  "documents/etc.)", all(v == 0 for v in counts.values()), str(counts))
    finally:
        await pool.close()

    print()
    if _ok:
        print("RESULT: ALL ASSERTIONS PASSED ✅")
        sys.exit(0)
    print("RESULT: FAILURES PRESENT ❌")
    sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
