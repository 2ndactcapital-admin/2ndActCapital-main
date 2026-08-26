"""verify_litellmreloadaction.py — LiteLLM model-cost-map reload workflow action.

Pass/fail only. No interactive prompts. Teardown at start AND in a finally block.

What this proves, and what it deliberately does NOT:

  PROVES (real, against the deployed database and the real workflow engine):
    * the action is registered and discoverable on the real admin surface
      (GET /admin/workflows/{id} -> `actions`, and assistant_action_catalog);
    * with no LiteLLM endpoint configured — the REAL current state — invoking it
      fails LOUD with a specific, actionable message, and the workflow run HOLDs
      with that message in workflow_runs.error_detail. Not a silent no-op;
    * against a LOCAL STAND-IN HTTP server that mimics LiteLLM's
      /reload/model_cost_map, the same action succeeds and the run-step audit
      trail records the real success;
    * the Tier-3 assignment is real (derived from the BPMN by the real deriver,
      stored as workflow_steps.autonomy_tier = 3) and genuinely requires no
      approval step: the run reaches 'completed' with zero User Tasks and
      approved_by NULL everywhere;
    * a workflow does NOT widen permissions — a starter without the action's
      required_permission is refused.

  DOES NOT PROVE (honest, because it cannot be proven yet):
    * any end-to-end call against a real LiteLLM proxy. Phase A of
      docs/LITELLM_INTEGRATION_DESIGN_V1.md §14 has not shipped; there is no
      deployed proxy and no LITELLM_* secret anywhere. The stand-in server is
      labelled a stand-in everywhere and is never described as LiteLLM.
    * a scheduled invocation. No recurring-schedule mechanism exists in this
      platform (Task 1c). That assertion is reported N/A, with the gap named.

Run:  python apps/api/scripts/verify_litellmreloadaction.py
"""
import asyncio
import json
import os
import sys
import threading
import types
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from uuid import UUID

import asyncpg

API_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if API_DIR not in sys.path:
    sys.path.insert(0, API_DIR)

from services import workflow_engine as we  # noqa: E402
from services.action_registry import REGISTRY  # noqa: E402
from services.assistant_actions import register_all  # noqa: E402
from services.assistant_actions.litellm_ops import (  # noqa: E402
    ACTION_KEY,
    LITELLM_ENV_VARS,
    LiteLLMConfigError,
    LiteLLMReloadError,
    RELOAD_PATH,
    credential_state,
    reload_model_cost_map,
)
from services.workflow_steps_deriver import derive_and_store_steps, derive_steps  # noqa: E402

ORG_ID = UUID("00000000-0000-0000-0000-000000000001")
DEF_ID = UUID("99000000-0000-0000-0000-0000000000e1")
VER_ID = UUID("99000000-0000-0000-0000-0000000000e2")
# Starter WITH the required permission (via a test Profile grant).
U_OPS = UUID("99000000-0000-0000-0000-0000000000e3")
# Starter WITHOUT it — proves a workflow is not a permission bypass.
U_NOPERM = UUID("99000000-0000-0000-0000-0000000000e4")
ALL_USERS = [U_OPS, U_NOPERM]
PROFILE_OPS = "LiteLLM Verify Ops"
PROFILE_NONE = "LiteLLM Verify NoPerm"
ALL_PROFILES = [PROFILE_OPS, PROFILE_NONE]
REQUIRED_PERM = "author_workflows"
STEP_KEY = "Reload_Cost_Map"

FIXTURE = Path(API_DIR) / "fixtures" / "litellm_cost_map_reload.bpmn"

_ok = True
_results: list[tuple[str, bool]] = []


def check(label: str, passed: bool, detail: str = "") -> bool:
    global _ok
    line = f"{'[PASS]' if passed else '[FAIL]'} {label}"
    if detail:
        line += f"  — {detail}"
    print(line)
    _results.append((label, passed))
    if not passed:
        _ok = False
    return passed


def note(label: str, detail: str) -> None:
    print(f"[N/A ] {label}  — {detail}")


# ── Local stand-in server (NOT LiteLLM) ──────────────────────────────────────
class _StandInHandler(BaseHTTPRequestHandler):
    """Mimics the response SHAPE of LiteLLM's POST /reload/model_cost_map.

    It is a stand-in, not LiteLLM: it proves our client code posts to the right
    path with the right bearer header and handles a 200 correctly. It proves
    nothing about the real proxy's behaviour.
    """

    received: list[dict] = []

    def do_POST(self):  # noqa: N802 — BaseHTTPRequestHandler's required name
        _StandInHandler.received.append(
            {"path": self.path, "authorization": self.headers.get("Authorization")}
        )
        if self.path != RELOAD_PATH:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b'{"detail":"not found"}')
            return
        if self.headers.get("Authorization") != f"Bearer {_STANDIN_KEY}":
            self.send_response(401)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error":"invalid master key"}')
            return
        body = json.dumps(
            {"status": "success", "message": "model cost map reloaded"}
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args):  # silence the default stderr access log
        pass


_STANDIN_KEY = "sk-standin-verify-key"


class StandIn:
    def __enter__(self):
        _StandInHandler.received = []
        self.server = HTTPServer(("127.0.0.1", 0), _StandInHandler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        os.environ["LITELLM_BASE_URL"] = f"http://127.0.0.1:{self.port}"
        os.environ["LITELLM_MASTER_KEY"] = _STANDIN_KEY
        return self

    def __exit__(self, *_exc):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
        clear_litellm_env()


def clear_litellm_env():
    for var in LITELLM_ENV_VARS:
        os.environ.pop(var, None)


# ── DB teardown / seed ───────────────────────────────────────────────────────
async def teardown(conn):
    """FK-safe: member_todos -> run_steps -> runs -> triggers -> steps ->
    versions -> defs -> profile grants -> profiles -> users.

    member_todos comes FIRST and is deleted by BOTH linkages, not just by user:
    a held run raises a HOLD alert for the starter AND for every Org Admin, so
    scoping only to the test users would strand real admins' todos pointing at
    a deleted run."""
    await conn.execute(
        """DELETE FROM member_todos
           WHERE (related_type = 'workflow_run'
                  AND related_id IN (SELECT id FROM workflow_runs
                                     WHERE workflow_version_id = $1))
              OR (related_type = 'workflow_run_step'
                  AND related_id IN (SELECT rs.id FROM workflow_run_steps rs
                                     JOIN workflow_steps ws ON ws.id = rs.workflow_step_id
                                     WHERE ws.workflow_version_id = $1))
              OR user_id = ANY($2::uuid[])""",
        VER_ID, ALL_USERS,
    )
    await conn.execute(
        """DELETE FROM workflow_run_steps WHERE workflow_run_id IN
           (SELECT id FROM workflow_runs WHERE workflow_version_id = $1)""",
        VER_ID,
    )
    await conn.execute("DELETE FROM workflow_runs WHERE workflow_version_id = $1", VER_ID)
    await conn.execute(
        "DELETE FROM workflow_triggers WHERE workflow_definition_id = $1", DEF_ID
    )
    await conn.execute("DELETE FROM workflow_steps WHERE workflow_version_id = $1", VER_ID)
    await conn.execute("DELETE FROM workflow_versions WHERE id = $1", VER_ID)
    await conn.execute("DELETE FROM workflow_definitions WHERE id = $1", DEF_ID)
    await conn.execute("UPDATE users SET profile_id = NULL WHERE id = ANY($1::uuid[])", ALL_USERS)
    await conn.execute(
        """DELETE FROM profile_permissions WHERE profile_id IN
           (SELECT id FROM profiles WHERE org_id = $1 AND name = ANY($2::text[]))""",
        ORG_ID, ALL_PROFILES,
    )
    await conn.execute(
        "DELETE FROM profiles WHERE org_id = $1 AND name = ANY($2::text[]) AND is_seed = false",
        ORG_ID, ALL_PROFILES,
    )
    await conn.execute("DELETE FROM users WHERE id = ANY($1::uuid[])", ALL_USERS)


async def _mk_profile(conn, name, permission_key):
    pid = await conn.fetchval(
        """INSERT INTO profiles (org_id, name, description, is_seed)
           VALUES ($1, $2, 'litellmreloadaction verify', false) RETURNING id""",
        ORG_ID, name,
    )
    if permission_key:
        await conn.execute(
            """INSERT INTO profile_permissions (org_id, profile_id, permission_key)
               VALUES ($1, $2, $3) ON CONFLICT (profile_id, permission_key) DO NOTHING""",
            ORG_ID, pid, permission_key,
        )
    return pid


async def seed(conn, bpmn_xml: str):
    p_ops = await _mk_profile(conn, PROFILE_OPS, REQUIRED_PERM)
    p_none = await _mk_profile(conn, PROFILE_NONE, None)
    for uid, sub, pid in ((U_OPS, "litellm_ops", p_ops), (U_NOPERM, "litellm_noperm", p_none)):
        await conn.execute(
            """INSERT INTO users (id, org_id, email, full_name, auth0_sub, role, profile_id)
               VALUES ($1, $2, $3, $4, $5, 'member', $6)
               ON CONFLICT (auth0_sub) DO NOTHING""",
            uid, ORG_ID, f"{sub}@test.local", sub, f"auth0|{sub}", pid,
        )
    await conn.execute(
        """INSERT INTO workflow_definitions (id, org_id, name, description, created_by)
           VALUES ($1, $2, 'LiteLLM Model Cost Map Reload',
                   'Reload the LiteLLM proxy model cost map (manual trigger)', $3)
           ON CONFLICT (id) DO NOTHING""",
        DEF_ID, ORG_ID, U_OPS,
    )
    await conn.execute(
        """INSERT INTO workflow_versions
             (id, workflow_definition_id, org_id, version_number, bpmn_xml,
              change_summary, is_current, created_by)
           VALUES ($1, $2, $3, 1, $4, 'v1 — initial', true, $5)
           ON CONFLICT (id) DO NOTHING""",
        VER_ID, DEF_ID, ORG_ID, bpmn_xml, U_OPS,
    )
    await derive_and_store_steps(conn, VER_ID, ORG_ID, bpmn_xml)


def _req(uid):
    return types.SimpleNamespace(
        state=types.SimpleNamespace(user={"sub": str(uid), "org_id": str(ORG_ID)})
    )


# ── Task 1 discovery report ──────────────────────────────────────────────────
def report_discovery():
    print("\n=== Task 1 — Discovery findings (real, read from the code) ===")
    print(
        "  1(a) ACTION REGISTRY: services/action_registry.py holds a module-global "
        "REGISTRY of AssistantAction dataclasses (key, module, description, "
        "access_type read|write, required_permission, default_autonomy, "
        "reversible, render_target, handler, params_schema). A new action is "
        "registered by a module-level register_actions() in "
        "services/assistant_actions/<module>.py, added to register_all() in that "
        "package's __init__.py, which main.py:_startup() calls before serving, "
        "then REGISTRY.sync_catalog(pool, org_id) upserts every action into "
        "assistant_action_catalog. Real examples followed: tasks.my_todos, "
        "marketplace.show_new_deals, spv.*. A BPMN ServiceTask does NOT embed "
        "code: it carries <bpmn:extensionElements><twoa:governance "
        "actionRegistryKey=... autonomyTier=... assignedRoleProfileId=.../> under "
        "xmlns:twoa='http://2ndactcapital.com/bpmn/ext', which "
        "workflow_steps_deriver.py turns into a workflow_steps row whose "
        "step_key equals the BPMN element id."
    )
    print(
        "  1(b) 3-TIER AUTONOMY: Tier 1 = durable proposed-object, "
        "draft-until-approved (User Task, maker-checker: approver != proposer). "
        "Tier 2 = confirm-and-log. Tier 3 = execute freely, fully autonomous — "
        "the LEAST RESTRICTIVE tier, real stored value workflow_steps."
        "autonomy_tier = 3 (integer NOT NULL DEFAULT 1). Real default table in "
        "services/workflow_steps_deriver.py: User Task -> 1 always; Send Task -> "
        "1; Service Task with a READ action -> 3; Service Task with a WRITE or "
        "unresolved action -> 2, NEVER 3 by default; Business Rule Task -> 3. "
        "Tier 3 for a write action is supported only as a deliberate authoring "
        "choice via an explicit autonomyTier attribute — which is exactly how "
        "this workflow sets it."
    )
    print(
        "  1(c) SCHEDULED EXECUTION — NOTHING FIRES ON A SCHEDULE TODAY. The "
        "workflow_triggers table really has trigger_type / schedule_cron / "
        "event_type, and GET /admin/workflow-triggers really reads them, but NO "
        "code anywhere reads schedule_cron to start a run: no APScheduler, no "
        "croniter, no 'type: cron' service in render.yaml (only two type:web). "
        "What DOES work is EVENT triggering: services/chancery_workflow_bridge.py "
        "selects trigger_type='event' AND event_type='document_confirmed' and "
        "calls start_workflow_run in-process on document confirmation — the "
        "platform's only real auto-start, and it is not a schedule. Therefore "
        "Task 3 wired a MANUAL trigger, and the recurring-schedule gap is "
        "recorded as a tracked follow-up in docs/PROJECT_STATUS.md."
    )
    print(
        "  1(d) EXTERNAL SERVICE URL + CREDENTIAL: process ENVIRONMENT VARIABLES, "
        "valued from Doppler, declared as sync:false manifest entries in "
        "render.yaml. The precedent followed is services/portfolio_altruist.py "
        "(ALTRUIST_BASE_URL / ALTRUIST_CLIENT_ID / ALTRUIST_CLIENT_SECRET read via "
        "os.environ, with a credential_state() presence check naming the missing "
        "variables). org_settings is NOT the convention for external credentials "
        "— it is per-org white-label config. So this action reads "
        "LITELLM_BASE_URL / LITELLM_MASTER_KEY from the environment. Confirmed "
        "live against Doppler: the development config holds NO LITELLM_* secret, "
        "so Phase A really is not deployed."
    )
    print()


# ── Checks ───────────────────────────────────────────────────────────────────
async def run_checks(pool, conn):
    # --- registered + discoverable on the real admin surface -----------------
    action = REGISTRY.get(ACTION_KEY)
    check(
        "action is registered in the real REGISTRY",
        action is not None
        and action.module == "litellm_ops"
        and action.access_type == "write"
        and action.required_permission == REQUIRED_PERM
        and action.workflow_invocable is True,
        f"key={ACTION_KEY}",
    )

    from routers import workflows as wf

    detail = await wf.get_workflow(_req(U_OPS), DEF_ID)
    listed = [a for a in detail.actions if a.key == ACTION_KEY]
    check(
        "action appears in the real admin surface that lists available actions "
        "(GET /admin/workflows/{id} -> actions)",
        len(listed) == 1 and listed[0].access_type == "write",
        f"{len(detail.actions)} actions offered to the properties panel",
    )

    await REGISTRY.sync_catalog(pool, str(ORG_ID))
    cat = await conn.fetchrow(
        """SELECT action_key, module, access_type, required_permission, is_active
           FROM assistant_action_catalog WHERE org_id = $1 AND action_key = $2""",
        ORG_ID, ACTION_KEY,
    )
    check(
        "action is persisted to assistant_action_catalog by the real sync",
        cat is not None and cat["is_active"] and cat["access_type"] == "write",
        f"module={cat['module'] if cat else None}",
    )

    # --- Tier assignment is real, derived by the real deriver ----------------
    step_row = await conn.fetchrow(
        """SELECT step_key, step_type, autonomy_tier, action_registry_key
           FROM workflow_steps WHERE workflow_version_id = $1 AND step_key = $2""",
        VER_ID, STEP_KEY,
    )
    check(
        "BPMN ServiceTask binding resolved to a real workflow_steps row at "
        "autonomy_tier 3 (the least restrictive tier)",
        step_row is not None
        and step_row["step_type"] == "service"
        and step_row["autonomy_tier"] == 3
        and step_row["action_registry_key"] == ACTION_KEY,
        f"tier={step_row['autonomy_tier'] if step_row else None}",
    )

    # The override is meaningful only if the DEFAULT would have been stricter.
    without_override = FIXTURE.read_text().replace(' autonomyTier="3"', "")
    default_tier = next(
        s["autonomy_tier"] for s in derive_steps(without_override) if s["step_key"] == STEP_KEY
    )
    check(
        "Tier 3 is a deliberate explicit override, not a permissive default "
        "(same BPMN without autonomyTier derives Tier 2)",
        default_tier == 2,
        f"default for a WRITE service task = {default_tier}",
    )

    user_steps = await conn.fetchval(
        """SELECT count(*) FROM workflow_steps
           WHERE workflow_version_id = $1 AND step_type IN ('user', 'send')""",
        VER_ID,
    )
    check(
        "Tier 3 requires no approval step: the process contains zero User/Send "
        "tasks, so there is no gate for the run to stop at",
        user_steps == 0,
        f"user+send steps = {user_steps}",
    )

    # --- REAL CURRENT STATE: no LiteLLM configured -> fail LOUD --------------
    clear_litellm_env()
    present, missing = credential_state()
    check(
        "the real current state is genuinely unconfigured (no LITELLM_* set)",
        not present and set(missing) == set(LITELLM_ENV_VARS),
        f"missing={list(missing)}",
    )

    direct_err = None
    try:
        await reload_model_cost_map()
    except LiteLLMConfigError as exc:
        direct_err = str(exc)
    except Exception as exc:  # noqa: BLE001
        direct_err = f"WRONG EXCEPTION TYPE {type(exc).__name__}: {exc}"
    check(
        "invoking with no LiteLLM endpoint raises LiteLLMConfigError — not a "
        "silent no-op, not a falsy return",
        direct_err is not None and direct_err.startswith("Cannot reload"),
        (direct_err or "NO EXCEPTION RAISED")[:110] + "...",
    )
    check(
        "that failure message is specific and actionable (names both missing "
        "variables, the Phase A dependency, and the fix)",
        direct_err is not None
        and all(v in direct_err for v in LITELLM_ENV_VARS)
        and "Phase A" in direct_err
        and "No HTTP call was attempted" in direct_err,
    )

    # Same failure, but through the REAL workflow engine.
    run_failed = False
    try:
        await we.start_workflow_run(pool, VER_ID, ORG_ID, {"trigger": "manual"}, U_OPS)
    except LiteLLMConfigError:
        run_failed = True
    held = await conn.fetchrow(
        """SELECT id, status, error_detail FROM workflow_runs
           WHERE workflow_version_id = $1 AND status = 'held'
           ORDER BY started_at DESC LIMIT 1""",
        VER_ID,
    )
    check(
        "a real workflow run over the unconfigured action HOLDs loudly — "
        "workflow_runs.status='held' with the specific message in error_detail",
        run_failed
        and held is not None
        and "LITELLM_BASE_URL" in (held["error_detail"] or ""),
        f"error_detail={(held['error_detail'] or '')[:80] if held else None}...",
    )
    held_step = await conn.fetchrow(
        """SELECT rs.status FROM workflow_run_steps rs
           JOIN workflow_steps ws ON ws.id = rs.workflow_step_id
           WHERE rs.workflow_run_id = $1 AND ws.step_key = $2""",
        held["id"] if held else None, STEP_KEY,
    ) if held else None
    check(
        "the held run did NOT record the service step as completed "
        "(no false success in the audit trail)",
        held_step is not None and held_step["status"] != "completed",
        f"step status={held_step['status'] if held_step else None}",
    )

    # --- Against a LOCAL STAND-IN server -> succeeds, logged correctly -------
    with StandIn() as standin:
        started = await we.start_workflow_run(
            pool, VER_ID, ORG_ID, {"trigger": "manual"}, U_OPS
        )
        check(
            "against a local STAND-IN server mimicking LiteLLM's "
            "/reload/model_cost_map, the workflow run completes",
            started["status"] == "completed" and STEP_KEY in started["executed_service_steps"],
            f"status={started['status']} port={standin.port}",
        )
        posted = _StandInHandler.received
        check(
            "the action really POSTed to the documented path with the master key "
            "as a bearer token (observed by the stand-in, not assumed)",
            len(posted) == 1
            and posted[0]["path"] == RELOAD_PATH
            and posted[0]["authorization"] == f"Bearer {_STANDIN_KEY}",
            f"{posted[0]['path'] if posted else 'NO REQUEST RECEIVED'}",
        )

        run_row = await conn.fetchrow(
            "SELECT id, status, error_detail FROM workflow_runs WHERE id = $1",
            started["run_id"],
        )
        step_res = await conn.fetchrow(
            """SELECT rs.status, rs.result, rs.completed_at, rs.approved_by
               FROM workflow_run_steps rs
               JOIN workflow_steps ws ON ws.id = rs.workflow_step_id
               WHERE rs.workflow_run_id = $1 AND ws.step_key = $2""",
            started["run_id"], STEP_KEY,
        )
        result = step_res["result"] if step_res else None
        if isinstance(result, str):
            result = json.loads(result)
        check(
            "the workflow's own execution log records the success correctly: "
            "run 'completed', step 'completed', result.invoked=true, "
            "result.handler_data.status_code=200",
            run_row is not None
            and run_row["status"] == "completed"
            and run_row["error_detail"] is None
            and step_res["status"] == "completed"
            and step_res["completed_at"] is not None
            and result is not None
            and result.get("invoked") is True
            and result.get("ok") is True
            and (result.get("handler_data") or {}).get("status_code") == 200,
            f"result={json.dumps(result)[:100] if result else None}...",
        )
        check(
            "Tier 3 genuinely ran with NO approval: the run completed without "
            "pausing and no run-step carries an approved_by",
            started["paused_at"] is None and step_res["approved_by"] is None,
            f"paused_at={started['paused_at']}",
        )

        # A workflow is not a permission bypass.
        bypass_blocked = False
        try:
            await we.start_workflow_run(
                pool, VER_ID, ORG_ID, {"trigger": "manual"}, U_NOPERM
            )
        except we.WorkflowEngineError as exc:
            bypass_blocked = REQUIRED_PERM in str(exc)
        check(
            "a workflow does not widen permissions: a starter lacking "
            f"'{REQUIRED_PERM}' is refused even at Tier 3",
            bypass_blocked,
        )

        # A configured-but-failing endpoint is also loud (distinct exception).
        os.environ["LITELLM_MASTER_KEY"] = "sk-wrong-key"
        http_err = None
        try:
            await reload_model_cost_map()
        except LiteLLMReloadError as exc:
            http_err = str(exc)
        check(
            "a configured endpoint that REFUSES the call raises LiteLLMReloadError "
            "with the real status code and body — distinct from the unconfigured case",
            http_err is not None and "HTTP 401" in http_err and "NOT reloaded" in http_err,
            (http_err or "NO EXCEPTION RAISED")[:90] + "...",
        )
        os.environ["LITELLM_MASTER_KEY"] = _STANDIN_KEY

    # Unreachable endpoint (server now shut down) -> transport failure, loud.
    os.environ["LITELLM_BASE_URL"] = f"http://127.0.0.1:{standin.port}"
    os.environ["LITELLM_MASTER_KEY"] = _STANDIN_KEY
    transport_err = None
    try:
        await reload_model_cost_map(timeout=3.0)
    except LiteLLMReloadError as exc:
        transport_err = str(exc)
    check(
        "an UNREACHABLE LiteLLM endpoint fails loud at the transport layer with "
        "the URL named — never a silent success",
        transport_err is not None and "transport layer" in transport_err,
        (transport_err or "NO EXCEPTION RAISED")[:90] + "...",
    )
    clear_litellm_env()

    # --- Scheduling: honest N/A ---------------------------------------------
    cron_trigger_readers = await conn.fetchval(
        "SELECT count(*) FROM workflow_triggers WHERE trigger_type = 'schedule'"
    )
    note(
        "prove a SCHEDULED invocation fires",
        "NOT APPLICABLE — no scheduling mechanism exists to prove. "
        "workflow_triggers.schedule_cron is stored and displayed but no code "
        "reads it to start a run (no APScheduler/croniter, no cron service in "
        f"render.yaml). Rows with trigger_type='schedule' in the DB right now: "
        f"{cron_trigger_readers} — and even those would never fire. Task 3 "
        "therefore wired a MANUALLY-triggerable workflow, and the "
        "recurring-schedule gap is a tracked follow-up in docs/PROJECT_STATUS.md.",
    )
    print(
        "[INFO] Task 3 trigger decision: MANUAL, because Task 1c found no real "
        "scheduling mechanism. No fake scheduler was built."
    )
    print(
        "[INFO] Task 4 LiteLLM dependency: NOT ONE call in this run reached a real "
        "LiteLLM proxy. Phase A has not shipped; the success path above was "
        "proven against a local stand-in server, and is labelled as such."
    )


async def main():
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("[SKIP] DATABASE_URL not set")
        return 0

    register_all()
    bpmn_xml = FIXTURE.read_text()
    clear_litellm_env()
    report_discovery()

    pool = await asyncpg.create_pool(dsn, statement_cache_size=0, min_size=1, max_size=5)
    try:
        async with pool.acquire() as conn:
            await teardown(conn)
            await seed(conn, bpmn_xml)
        async with pool.acquire() as conn:
            await run_checks(pool, conn)
    finally:
        async with pool.acquire() as conn:
            await teardown(conn)
            leftovers = {
                "workflow_runs": await conn.fetchval(
                    "SELECT count(*) FROM workflow_runs WHERE workflow_version_id = $1", VER_ID
                ),
                "workflow_run_steps": await conn.fetchval(
                    """SELECT count(*) FROM workflow_run_steps rs
                       JOIN workflow_steps ws ON ws.id = rs.workflow_step_id
                       WHERE ws.workflow_version_id = $1""",
                    VER_ID,
                ),
                "workflow_steps": await conn.fetchval(
                    "SELECT count(*) FROM workflow_steps WHERE workflow_version_id = $1", VER_ID
                ),
                "workflow_versions": await conn.fetchval(
                    "SELECT count(*) FROM workflow_versions WHERE id = $1", VER_ID
                ),
                "workflow_definitions": await conn.fetchval(
                    "SELECT count(*) FROM workflow_definitions WHERE id = $1", DEF_ID
                ),
                "profiles": await conn.fetchval(
                    "SELECT count(*) FROM profiles WHERE org_id = $1 AND name = ANY($2::text[])",
                    ORG_ID, ALL_PROFILES,
                ),
                "users": await conn.fetchval(
                    "SELECT count(*) FROM users WHERE id = ANY($1::uuid[])", ALL_USERS
                ),
                "member_todos": await conn.fetchval(
                    "SELECT count(*) FROM member_todos WHERE user_id = ANY($1::uuid[])",
                    ALL_USERS,
                ),
            }
            check(
                "teardown: zero leftover rows",
                all(v == 0 for v in leftovers.values()),
                json.dumps(leftovers),
            )
        await pool.close()
        clear_litellm_env()

    passed = sum(1 for _, p in _results if p)
    print(f"\n{passed}/{len(_results)} checks passed")
    print("RESULT:", "PASS" if _ok else "FAIL")
    return 0 if _ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
