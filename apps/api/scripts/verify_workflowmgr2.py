"""verify_workflowmgr2.py — Workflow Manager Phase 2 (NL → BPMN generation).

Proves the Phase-2 generation SERVICE end to end against real seeded data:

  * the generic BPMN → workflow_steps deriver (services/workflow_steps_deriver.py)
  * the autonomy-tier default table, including the load-bearing nuance that a
    WRITE-capable Service Task is Tier 2 (confirm-and-log), NOT Tier 3
  * the NL → BPMN generator (services/workflow_nl_generator.py), routed through
    the Sprint-27 TaskRouter helper, validated against real Profiles + real
    action-registry keys before anything is stored
  * the structural "generate once, never regenerate" guarantee (Task 4)

Pass/fail only. No interactive prompts. Teardown at start AND at end.

HERMETIC AI: there is no live ANTHROPIC_API_KEY in local/CI shells and we must
not make a paid call to prove generation wiring. So — exactly as verify_s27 does
— we inject a FAKE ``anthropic`` module at the seam ``_execute_chain`` constructs
its client from. The fake returns BPMN XML we queue per test (built from the
REAL seeded profile + REAL action keys the generator will validate against), so
the REAL validation, REAL deriver, REAL retry loop and REAL DB writes all run;
only the network boundary is faked.

Run:  python apps/api/scripts/verify_workflowmgr2.py
"""
import asyncio
import inspect
import os
import sys
import types
from pathlib import Path
from uuid import UUID

import asyncpg

API_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if API_DIR not in sys.path:
    sys.path.insert(0, API_DIR)

from services.action_registry import REGISTRY
from services.assistant_actions import register_all
from services.workflow_engine import parse_bpmn
from services.workflow_steps_deriver import derive_steps, derive_and_store_steps
import services.workflow_nl_generator as nlgen
from services.workflow_nl_generator import generate_workflow, WorkflowGenerationError

ORG_ID = UUID("00000000-0000-0000-0000-000000000001")
TEST_DEF_ID = UUID("99000000-0000-0000-0000-0000000000e1")
TEST_VERSION_ID = UUID("99000000-0000-0000-0000-0000000000e2")
CREATOR_ID = UUID("99000000-0000-0000-0000-0000000000e9")
NAME_MARKER = "WFMGR2_TEST"

READ_ACTION = "marketplace.show_new_deals"   # access_type = read
WRITE_ACTION = "spv.record_transaction"      # access_type = write

BPMN_NS = "http://www.omg.org/spec/BPMN/20100524/MODEL"
EXT_NS = "http://2ndactcapital.com/bpmn/ext"

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


# ── hermetic fake anthropic (same seam as verify_s27) ────────────────────────
_RESPONSE_QUEUE: list[str] = []


class _FakeUsage:
    def __init__(self, i, o):
        self.input_tokens = i
        self.output_tokens = o


class _FakeBlock:
    def __init__(self, text):
        self.text = text
        self.type = "text"


class _FakeMessage:
    def __init__(self, text):
        self.content = [_FakeBlock(text)]
        self.usage = _FakeUsage(200, 300)
        self.stop_reason = "end_turn"


class _FakeMessages:
    async def create(self, *, model, max_tokens=None, system=None, messages=None, tools=None, **_):
        await asyncio.sleep(0.001)
        if not _RESPONSE_QUEUE:
            raise RuntimeError("fake anthropic: response queue exhausted")
        return _FakeMessage(_RESPONSE_QUEUE.pop(0))


class _FakeAsyncAnthropic:
    def __init__(self, *a, **k):
        self.messages = _FakeMessages()


def _install_fake_anthropic():
    mod = types.ModuleType("anthropic")
    mod.AsyncAnthropic = _FakeAsyncAnthropic
    sys.modules["anthropic"] = mod


# ── BPMN builders ────────────────────────────────────────────────────────────
def _deriver_fixture(profile_id) -> str:
    """A process exercising every actionable type + a gateway (no row for it)."""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<bpmn:definitions xmlns:bpmn="{BPMN_NS}" xmlns:twoa="{EXT_NS}" '
        'id="D_wfmgr2_deriver" targetNamespace="http://2ndactcapital.com/bpmn">'
        '<bpmn:process id="wfmgr2_deriver_proc" isExecutable="true">'
        '<bpmn:startEvent id="Start_1"><bpmn:outgoing>f1</bpmn:outgoing></bpmn:startEvent>'
        '<bpmn:serviceTask id="Read_1" name="Show deals">'
        f'<bpmn:extensionElements><twoa:governance actionRegistryKey="{READ_ACTION}"/></bpmn:extensionElements>'
        '<bpmn:incoming>f1</bpmn:incoming><bpmn:outgoing>f2</bpmn:outgoing></bpmn:serviceTask>'
        '<bpmn:serviceTask id="Write_1" name="Record txn">'
        f'<bpmn:extensionElements><twoa:governance actionRegistryKey="{WRITE_ACTION}"/></bpmn:extensionElements>'
        '<bpmn:incoming>f2</bpmn:incoming><bpmn:outgoing>f3</bpmn:outgoing></bpmn:serviceTask>'
        '<bpmn:userTask id="User_1" name="Member reviews">'
        f'<bpmn:extensionElements><twoa:governance assignedRoleProfileId="{profile_id}"/></bpmn:extensionElements>'
        '<bpmn:incoming>f3</bpmn:incoming><bpmn:outgoing>f4</bpmn:outgoing></bpmn:userTask>'
        '<bpmn:exclusiveGateway id="Gw_1"><bpmn:incoming>f4</bpmn:incoming>'
        '<bpmn:outgoing>f5</bpmn:outgoing></bpmn:exclusiveGateway>'
        '<bpmn:sendTask id="Send_1" name="Notify member"><bpmn:incoming>f5</bpmn:incoming>'
        '<bpmn:outgoing>f6</bpmn:outgoing></bpmn:sendTask>'
        '<bpmn:businessRuleTask id="Rule_1" name="Score deal"><bpmn:incoming>f6</bpmn:incoming>'
        '<bpmn:outgoing>f7</bpmn:outgoing></bpmn:businessRuleTask>'
        '<bpmn:endEvent id="End_1"><bpmn:incoming>f7</bpmn:incoming></bpmn:endEvent>'
        '<bpmn:sequenceFlow id="f1" sourceRef="Start_1" targetRef="Read_1"/>'
        '<bpmn:sequenceFlow id="f2" sourceRef="Read_1" targetRef="Write_1"/>'
        '<bpmn:sequenceFlow id="f3" sourceRef="Write_1" targetRef="User_1"/>'
        '<bpmn:sequenceFlow id="f4" sourceRef="User_1" targetRef="Gw_1"/>'
        '<bpmn:sequenceFlow id="f5" sourceRef="Gw_1" targetRef="Send_1"/>'
        '<bpmn:sequenceFlow id="f6" sourceRef="Send_1" targetRef="Rule_1"/>'
        '<bpmn:sequenceFlow id="f7" sourceRef="Rule_1" targetRef="End_1"/>'
        '</bpmn:process></bpmn:definitions>'
    )


def _generated_bpmn(profile_id, action_key) -> str:
    """A minimal valid process the fake model 'produces' — references REAL rows."""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<bpmn:definitions xmlns:bpmn="{BPMN_NS}" xmlns:twoa="{EXT_NS}" '
        'id="D_gen" targetNamespace="http://2ndactcapital.com/bpmn">'
        '<bpmn:process id="wfmgr2_generated_proc" isExecutable="true">'
        '<bpmn:startEvent id="g_start"><bpmn:outgoing>gf1</bpmn:outgoing></bpmn:startEvent>'
        '<bpmn:serviceTask id="g_service" name="Pull latest deals">'
        f'<bpmn:extensionElements><twoa:governance actionRegistryKey="{action_key}"/></bpmn:extensionElements>'
        '<bpmn:incoming>gf1</bpmn:incoming><bpmn:outgoing>gf2</bpmn:outgoing></bpmn:serviceTask>'
        '<bpmn:userTask id="g_user" name="Member approves">'
        f'<bpmn:extensionElements><twoa:governance assignedRoleProfileId="{profile_id}"/></bpmn:extensionElements>'
        '<bpmn:incoming>gf2</bpmn:incoming><bpmn:outgoing>gf3</bpmn:outgoing></bpmn:userTask>'
        '<bpmn:endEvent id="g_end"><bpmn:incoming>gf3</bpmn:incoming></bpmn:endEvent>'
        '<bpmn:sequenceFlow id="gf1" sourceRef="g_start" targetRef="g_service"/>'
        '<bpmn:sequenceFlow id="gf2" sourceRef="g_service" targetRef="g_user"/>'
        '<bpmn:sequenceFlow id="gf3" sourceRef="g_user" targetRef="g_end"/>'
        '</bpmn:process></bpmn:definitions>'
    )


def _bad_ref_bpmn() -> str:
    """Well-formed BPMN that references a HALLUCINATED action key (must be rejected)."""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<bpmn:definitions xmlns:bpmn="{BPMN_NS}" xmlns:twoa="{EXT_NS}" '
        'id="D_bad" targetNamespace="http://2ndactcapital.com/bpmn">'
        '<bpmn:process id="wfmgr2_bad_proc" isExecutable="true">'
        '<bpmn:startEvent id="b_start"><bpmn:outgoing>bf1</bpmn:outgoing></bpmn:startEvent>'
        '<bpmn:serviceTask id="b_service" name="Do a made-up thing">'
        '<bpmn:extensionElements><twoa:governance actionRegistryKey="totally.invented_action"/></bpmn:extensionElements>'
        '<bpmn:incoming>bf1</bpmn:incoming><bpmn:outgoing>bf2</bpmn:outgoing></bpmn:serviceTask>'
        '<bpmn:endEvent id="b_end"><bpmn:incoming>bf2</bpmn:incoming></bpmn:endEvent>'
        '<bpmn:sequenceFlow id="bf1" sourceRef="b_start" targetRef="b_service"/>'
        '<bpmn:sequenceFlow id="bf2" sourceRef="b_service" targetRef="b_end"/>'
        '</bpmn:process></bpmn:definitions>'
    )


# ── DB teardown ──────────────────────────────────────────────────────────────
async def teardown(conn):
    def_rows = await conn.fetch(
        "SELECT id FROM workflow_definitions WHERE org_id=$1 AND name LIKE $2",
        ORG_ID, NAME_MARKER + "%",
    )
    def_ids = [r["id"] for r in def_rows] + [TEST_DEF_ID]
    await conn.execute(
        """DELETE FROM workflow_steps WHERE workflow_version_id IN
           (SELECT id FROM workflow_versions WHERE workflow_definition_id = ANY($1::uuid[]))""",
        def_ids,
    )
    await conn.execute("DELETE FROM workflow_steps WHERE workflow_version_id = $1", TEST_VERSION_ID)
    await conn.execute(
        "DELETE FROM workflow_versions WHERE workflow_definition_id = ANY($1::uuid[])", def_ids
    )
    await conn.execute("DELETE FROM workflow_definitions WHERE id = ANY($1::uuid[])", def_ids)
    await conn.execute("DELETE FROM users WHERE id = $1", CREATOR_ID)


async def seed(conn):
    await conn.execute(
        """
        INSERT INTO users (id, org_id, email, full_name, auth0_sub, role)
        VALUES ($1, $2, 'wfmgr2_creator@test.local', 'WFMGR2 Creator', 'auth0|wfmgr2_creator', 'member')
        ON CONFLICT (auth0_sub) DO NOTHING
        """,
        CREATOR_ID, ORG_ID,
    )


async def main():
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("SKIP — DATABASE_URL not set")
        sys.exit(0)

    register_all()
    _install_fake_anthropic()
    os.environ["ANTHROPIC_API_KEY"] = "sk-verify-wfmgr2-hermetic"

    pool = await asyncpg.create_pool(url, statement_cache_size=0, min_size=1, max_size=4)
    try:
        async with pool.acquire() as conn:
            await teardown(conn)  # teardown at start
            await seed(conn)

            profile = await conn.fetchrow(
                "SELECT id, name FROM profiles WHERE org_id=$1 AND is_seed=true ORDER BY name LIMIT 1",
                ORG_ID,
            )
            if profile is None:
                profile = await conn.fetchrow(
                    "SELECT id, name FROM profiles WHERE org_id=$1 ORDER BY name LIMIT 1", ORG_ID
                )
            profile_id = profile["id"]

            # ── [Y] Task 1 — three discovery findings, reported explicitly ────
            print("\n=== Task 1 — Discovery Findings ===")
            wfmgr1_verify = Path(API_DIR) / "scripts" / "verify_workflowmgr1.py"
            manual_insert = "INSERT INTO workflow_steps" in wfmgr1_verify.read_text()
            print(
                "  1(a) NO generic deriver existed in Phase 1: verify_workflowmgr1.py's "
                "seed_process() inserts workflow_steps rows BY HAND "
                f"(INSERT present={manual_insert}). Phase 2 BUILDS the generic deriver "
                "(services/workflow_steps_deriver.py) as a prerequisite."
            )
            read_act = REGISTRY.get(READ_ACTION)
            write_act = REGISTRY.get(WRITE_ACTION)
            print(
                "  1(b) action registry = services.action_registry.REGISTRY; read vs write is "
                "AssistantAction.access_type, a Literal['read','write'] string. "
                f"e.g. {READ_ACTION}={read_act.access_type}, {WRITE_ACTION}={write_act.access_type}."
            )
            uses_taskrouter = hasattr(nlgen, "call_claude_text")
            print(
                "  1(c) TaskRouter (Sprint 27) IS merged/working: the central AI helpers "
                "call_claude_text / call_claude_json (services/extraction.py) route through "
                "_execute_chain (per-org fallback chain + ai_decision_log). NL generation "
                f"calls through call_claude_text (imported={uses_taskrouter}), not the SDK."
            )
            check(
                "Task 1 findings reported (a: manual insert / no deriver, b: access_type, c: TaskRouter)",
                manual_insert and read_act.access_type == "read"
                and write_act.access_type == "write" and uses_taskrouter,
            )

            # ── [Y] Deriver: correct step_type per element, gateway excluded ──
            print("\n=== Task 2 — Generic deriver ===")
            await conn.execute(
                """INSERT INTO workflow_definitions (id, org_id, name, description, created_by)
                   VALUES ($1,$2,$3,'deriver fixture',$4) ON CONFLICT (id) DO NOTHING""",
                TEST_DEF_ID, ORG_ID, NAME_MARKER + " Deriver Definition", CREATOR_ID,
            )
            await conn.execute(
                """INSERT INTO workflow_versions
                     (id, workflow_definition_id, org_id, version_number, bpmn_xml,
                      change_summary, is_current, created_by)
                   VALUES ($1,$2,$3,1,$4,'fixture',true,$5) ON CONFLICT (id) DO NOTHING""",
                TEST_VERSION_ID, TEST_DEF_ID, ORG_ID, _deriver_fixture(profile_id), CREATOR_ID,
            )
            async with conn.transaction():
                await derive_and_store_steps(conn, TEST_VERSION_ID, ORG_ID, _deriver_fixture(profile_id))

            rows = await conn.fetch(
                """SELECT step_key, step_type, autonomy_tier, action_registry_key,
                          assigned_role_profile_id
                   FROM workflow_steps WHERE workflow_version_id=$1 ORDER BY step_key""",
                TEST_VERSION_ID,
            )
            by_key = {r["step_key"]: r for r in rows}
            expected_types = {
                "Read_1": "service", "Write_1": "service", "User_1": "user",
                "Send_1": "send", "Rule_1": "business_rule",
            }
            types_ok = (
                set(by_key) == set(expected_types)
                and all(by_key[k]["step_type"] == t for k, t in expected_types.items())
            )
            check(
                "deriver creates one row per actionable element with correct step_type "
                "(gateway + events excluded)",
                types_ok and "Gw_1" not in by_key and "Start_1" not in by_key,
                f"{ {k: v['step_type'] for k, v in by_key.items()} }, rows={len(rows)}",
            )

            # ── [Y] Service, READ-only action → Tier 3 ───────────────────────
            check(
                "Service Task wrapping a KNOWN READ-only action defaults to Tier 3",
                by_key.get("Read_1") and by_key["Read_1"]["autonomy_tier"] == 3,
                f"Read_1 tier={by_key['Read_1']['autonomy_tier']} action={by_key['Read_1']['action_registry_key']}",
            )

            # ── [Y] Service, WRITE-capable action → Tier 2, NOT Tier 3 ───────
            check(
                "Service Task wrapping a KNOWN WRITE-capable action defaults to Tier 2 (NOT Tier 3)",
                by_key.get("Write_1") and by_key["Write_1"]["autonomy_tier"] == 2,
                f"Write_1 tier={by_key['Write_1']['autonomy_tier']} action={by_key['Write_1']['action_registry_key']}",
            )

            # ── [Y] User Task → Tier 1 regardless ────────────────────────────
            check(
                "User Task always defaults to Tier 1 (regardless of assigned profile)",
                by_key.get("User_1") and by_key["User_1"]["autonomy_tier"] == 1
                and by_key["User_1"]["assigned_role_profile_id"] == profile_id,
                f"User_1 tier={by_key['User_1']['autonomy_tier']} profile={by_key['User_1']['assigned_role_profile_id']}",
            )

            # ── [Y] NL generation produces XML that parses via SpiffWorkflow ──
            print("\n=== Task 3 — NL → BPMN generation (hermetic) ===")
            _RESPONSE_QUEUE.clear()
            _RESPONSE_QUEUE.append(_generated_bpmn(profile_id, READ_ACTION))
            result = await generate_workflow(
                pool, org_id=ORG_ID, description="When a new deal arrives, pull the latest "
                "deals and have a member approve it.", created_by=CREATOR_ID,
                name=NAME_MARKER + " Happy Path",
            )
            gen_version = await conn.fetchrow(
                "SELECT version_number, is_current, bpmn_xml FROM workflow_versions WHERE id=$1",
                result["workflow_version_id"],
            )
            spec, proc_id = parse_bpmn(gen_version["bpmn_xml"])
            check(
                "NL generation produces BPMN that parses via SpiffWorkflow, stored as v1/is_current",
                spec is not None and gen_version["version_number"] == 1
                and gen_version["is_current"] is True,
                f"process_id={proc_id}, v={gen_version['version_number']}, current={gen_version['is_current']}",
            )

            # ── [Y] Every embedded ref resolves to a REAL existing row ────────
            derived = derive_steps(gen_version["bpmn_xml"])
            prof_ids_in_db = {
                r["id"] for r in await conn.fetch(
                    "SELECT id FROM profiles WHERE org_id=$1", ORG_ID
                )
            }
            all_refs_real = True
            detail = []
            for s in derived:
                if s["action_registry_key"]:
                    if REGISTRY.get(s["action_registry_key"]) is None:
                        all_refs_real = False
                        detail.append(f"bad action {s['action_registry_key']}")
                if s["assigned_role_profile_id"]:
                    if UUID(s["assigned_role_profile_id"]) not in prof_ids_in_db:
                        all_refs_real = False
                        detail.append(f"bad profile {s['assigned_role_profile_id']}")
            stored_steps = await conn.fetch(
                "SELECT count(*) AS n FROM workflow_steps WHERE workflow_version_id=$1",
                result["workflow_version_id"],
            )
            check(
                "every action_registry_key + assigned_role_profile_id in generated XML resolves "
                "to a REAL row (no hallucinated refs)",
                all_refs_real and stored_steps[0]["n"] == 2,
                f"steps_stored={stored_steps[0]['n']} {('; '.join(detail)) if detail else ''}",
            )

            # ── retry-once: invalid ref first, then valid → SUCCEEDS ─────────
            _RESPONSE_QUEUE.clear()
            _RESPONSE_QUEUE.append(_bad_ref_bpmn())                       # rejected
            _RESPONSE_QUEUE.append(_generated_bpmn(profile_id, READ_ACTION))  # corrected
            retry_result = await generate_workflow(
                pool, org_id=ORG_ID, description="Retry path.", created_by=CREATOR_ID,
                name=NAME_MARKER + " Retry",
            )
            retry_xml = await conn.fetchval(
                "SELECT bpmn_xml FROM workflow_versions WHERE id=$1", retry_result["workflow_version_id"]
            )
            check(
                "invalid-reference generation triggers ONE error-correction retry, then stores the valid XML",
                retry_result["workflow_version_id"] is not None
                and "totally.invented_action" not in retry_xml,
                f"queue_left={len(_RESPONSE_QUEUE)}",
            )

            # ── invalid twice → FAILS clearly, stores NOTHING ────────────────
            _RESPONSE_QUEUE.clear()
            _RESPONSE_QUEUE.append(_bad_ref_bpmn())
            _RESPONSE_QUEUE.append(_bad_ref_bpmn())
            defs_before = await conn.fetchval(
                "SELECT count(*) FROM workflow_definitions WHERE org_id=$1 AND name=$2",
                ORG_ID, NAME_MARKER + " ShouldFail",
            )
            raised = False
            try:
                await generate_workflow(
                    pool, org_id=ORG_ID, description="Always invalid.", created_by=CREATOR_ID,
                    name=NAME_MARKER + " ShouldFail",
                )
            except WorkflowGenerationError:
                raised = True
            defs_after = await conn.fetchval(
                "SELECT count(*) FROM workflow_definitions WHERE org_id=$1 AND name=$2",
                ORG_ID, NAME_MARKER + " ShouldFail",
            )
            check(
                "persistently-invalid generation FAILS loudly and stores NO definition/version",
                raised and defs_before == 0 and defs_after == 0,
                f"raised={raised} defs_after={defs_after}",
            )

            # ── [Y] Task 4 — regeneration structurally impossible ────────────
            print("\n=== Task 4 — generate once, never regenerate ===")
            sig = inspect.signature(generate_workflow)
            params = set(sig.parameters)
            forbidden = {
                p for p in params
                if ("definition_id" in p or "version_id" in p or p in {"workflow_id"})
            }
            src = inspect.getsource(generate_workflow)
            no_version_update = "UPDATE workflow_versions" not in src
            check(
                "generate_workflow has NO existing-definition/version parameter AND no UPDATE of "
                "workflow_versions — regeneration is structurally impossible",
                not forbidden and no_version_update,
                f"params={sorted(params)}; updates_versions={not no_version_update}",
            )

            # ── [Y] Teardown: zero leftover rows ─────────────────────────────
            print("\n=== Teardown ===")
            all_def_ids = [TEST_DEF_ID, result["workflow_definition_id"],
                           retry_result["workflow_definition_id"]]
            all_ver_ids = [TEST_VERSION_ID, result["workflow_version_id"],
                           retry_result["workflow_version_id"]]
            await teardown(conn)
            leftover = {}
            leftover["definitions"] = await conn.fetchval(
                "SELECT count(*) FROM workflow_definitions WHERE id = ANY($1::uuid[])", all_def_ids
            )
            leftover["versions"] = await conn.fetchval(
                "SELECT count(*) FROM workflow_versions WHERE id = ANY($1::uuid[])", all_ver_ids
            )
            leftover["steps"] = await conn.fetchval(
                "SELECT count(*) FROM workflow_steps WHERE workflow_version_id = ANY($1::uuid[])",
                all_ver_ids,
            )
            leftover["marker_defs"] = await conn.fetchval(
                "SELECT count(*) FROM workflow_definitions WHERE org_id=$1 AND name LIKE $2",
                ORG_ID, NAME_MARKER + "%",
            )
            leftover["users"] = await conn.fetchval(
                "SELECT count(*) FROM users WHERE id=$1", CREATOR_ID
            )
            check("Teardown left zero leftover rows", all(v == 0 for v in leftover.values()), str(leftover))

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
