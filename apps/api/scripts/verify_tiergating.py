"""verify_tiergating.py — a Tier-1 Service Task genuinely suspends.

THE GAP (confirmed live by wave2discovery.lowrisk, see
docs/WORKFLOW_WAVE2_DISCOVERY.md Task 5): ``services/workflow_engine.py``'s
``_drive`` loop auto-completed every ``bpmn:serviceTask`` the instant
SpiffWorkflow parked it in ``STARTED``, regardless of tier.
``_execute_service_task`` read only ``action_registry_key``;
``workflow_steps.autonomy_tier`` was fetched but used ONLY for a display
count (``routers/workflows.py``'s ``approval_step_count``). A Tier-1 verb
invoked from a Service Task simply executed, unattended, the instant the
engine reached it — exactly like a Tier-3 verb. Worse, the bpmn-js diagram
editor's properties panel already offered "Tier 1 — approval required" with
no engine effect at all.

THE FIX (already committed, this script proves it, does not build it):
  * ``services.workflow_engine.compute_effective_tier`` — effective tier is
    ``min(registry_tier, diagram_tier)``. Tier 1 is the DANGEROUS end (same
    convention as ``AssistantAction.tier``), so ``min()`` is the MORE
    restrictive of the two: an author may UPGRADE a Tier-3 verb (mark it
    Tier 1 in the diagram — it now suspends), but can never DOWNGRADE a
    Tier-1 verb (mark it Tier 3 in the diagram — it still suspends). Same
    most-restrictive-wins rule CLAUDE.md documents for dual-path permission
    resolution.
  * ``services.workflow_engine._drive`` now returns
    ``(executed, suspended_step_key)``. When the next STARTED Service Task's
    effective tier is 1 AND the action is ``workflow_invocable``, the loop
    returns WITHOUT calling ``_execute_service_task`` — the task stays
    parked, unexecuted.
  * ``services.workflow_engine._suspend_step`` (shared by
    ``start_workflow_run``, ``complete_user_task``'s post-approval
    continuation, and ``resolve_tier_approval``) creates an
    ``agent_proposals`` row (``proposed_by`` = the run's own
    ``started_by`` — see Task 1c below), sets
    ``workflow_run_steps.status='suspended'`` (linked via the new
    ``agent_proposal_id`` column, migration
    ``tiergating_agent_proposal_link.sql``), sets
    ``workflow_runs.status='awaiting_approval'`` with the state serialized,
    and alerts every ``review_agent_proposals`` holder
    (``workflow_todos.create_tier_approval_alerts``).
  * ``services.workflow_engine.resolve_tier_approval`` — approve/reject.
    Eligibility (maker-checker, or disclosed self-approval under a
    genuinely empty checker set) is ENTIRELY
    ``services.agent_proposals.review_proposal``'s job, not reimplemented.
    Approval invokes the verb EXACTLY ONCE, directly, then resumes driving.
    Rejection means the verb never executes; the run ends ``'rejected'``.
  * ``routers/workflows.py``'s new
    ``POST /admin/workflow-runs/{run_id}/steps/{step_id}/decision``, gated
    by a NEW role-based helper, ``_require_review_permission`` — NOT the
    existing Profile-based ``_require_workflow_permission``. This mattered:
    ``review_agent_proposals`` was granted via the ROLE system
    (``agenticmakerchecker_substrate.sql``), a DIFFERENT permission system
    than the Profile grants ``author_workflows`` / ``view_workflow_runs`` /
    ``configure_workflow_triggers`` use. Gating the endpoint with the
    Profile-based helper would have checked a permission NO real
    ``review_agent_proposals`` holder actually has, locking out every real
    reviewer. Caught and fixed in this same sprint, before this script was
    written — see ``services.agent_proposals.holds_review_permission``
    (renamed public from ``_holds_review_permission`` for this reuse).
  * ``GET /admin/workflow-runs/{run_id}`` now returns ``effective_tier`` per
    step, computed the SAME way the engine computes it, and
    ``permissions.can_review`` (also role-based).

TASK 1 FINDINGS (reported live, not assumed):
  1a. The exact point in ``_drive`` where a Service Task used to
      auto-complete unconditionally (now conditional on effective tier), and
      that resumption needs NOTHING beyond what already exists: a suspended
      run's state is captured by the SAME ``BpmnWorkflowSerializer`` /
      ``spiff_serialized_state`` mechanism already used to pause at a User
      Task — a STARTED Service Task serializes and deserializes exactly like
      a READY User Task; no new SpiffWorkflow machinery was needed.
  1b. What Tier 2 ("confirm & log") should do: NOTHING new. Every Service
      Task, at every tier, already writes a real result to
      ``workflow_run_steps.result`` (action_registry_key / resolved /
      invoked / handler_data) — that IS the "log" half. "Confirm" refers to
      a human being ABLE to inspect the run afterward via the run console,
      never a real-time approval gate — the diagram editor's own label says
      "confirm & log", not "approve", and only Tier 1's label says
      "approval required". So Tier 2 continues to auto-execute exactly like
      Tier 3; the only NEW engine behavior this sprint adds is Tier 1's
      suspend.
  1c. ``workflow_triggers.created_by`` IS recorded, and was ALREADY being
      passed through as ``workflow_runs.started_by`` by
      ``services.workflow_scheduler._fire`` before this sprint — confirmed
      by source inspection below, not assumed. The "scheduled run has
      started_by=NULL" premise in this sprint's own prompt does not hold
      against the live code.
  1d. The run-status vocabulary (``routers/workflows.py::RUN_STATUSES``) did
      NOT have an "awaiting approval" status before this sprint; it now
      does (``'awaiting_approval'``), plus a new terminal ``'rejected'``.

THE STAFFING FACT, stated plainly: in the live 2nd Act org, only
``org_admin`` holds ``review_agent_proposals`` (one user) — so in
production, most Tier-1 approvals take the disclosed self-approval path,
not a genuine second-reviewer path. This script does NOT touch the real
2nd Act org; it builds its own fixture org with a maker and TWO reviewers
so the genuine (non-self) maker-checker path is exercised end to end.

Hydrates DATABASE_URL from Doppler over HTTPS at startup (the
verify_scripttaskrefusal.py / verify_selfapproval.py pattern). Never prints
a credential value. RLS context is set on every connection that touches an
RLS-protected table — ``SELECT set_config('app.is_super_admin', 'true',
true)`` inside an explicit transaction for raw fixture/query connections
(CLAUDE.md's ``platform_scope()`` convention), and
``services.database.set_rls_context`` / ``reset_rls_context`` around every
call into the real service layer through the real RLS-wrapped pool.

WHAT THIS PROVES:
  [Y] Task 1's four findings, reported live.
  [Y] A Tier-1 step does not execute — the fake verb's CALL_COUNTS entry is
      genuinely absent/unchanged while the run sits 'awaiting_approval',
      not merely "the run status changed".
  [Y] A Tier-1 verb marked Tier 3 in the diagram STILL suspends (the
      DOWNGRADE case) — most-restrictive-wins.
  [Y] A Tier-3 verb marked Tier 1 in the diagram DOES suspend (the UPGRADE
      case) — the author can upgrade.
  [Y] Approval resumes the run and the verb executes EXACTLY ONCE (call
      count delta == 1, proven twice: once by the count, once by a second
      approval attempt being refused outright because the step is no
      longer 'suspended').
  [Y] Rejection means the verb never executes (call count stays 0; run ends
      'rejected').
  [Y] The maker cannot approve their own suspended step when another
      eligible reviewer exists (MakerCheckerError, both service-layer and
      as an HTTP 403).
  [Y] A scheduled run's maker is resolved per Task 1c's finding (already
      correct), and a run with NO resolvable maker is refused loudly
      (held + alerted + exception propagated), never silently treated as
      "anyone may approve".
  [Y] A Tier-3 step still executes immediately — no regression.
  [Y] The run console (``GET .../workflow-runs/{id}``) shows the EFFECTIVE
      tier, not the diagram's raw value.
  [Y] Cross-org: org B's reviewer cannot reach org A's suspended step
      (404 at the real HTTP boundary — the router's own org-ownership
      check, not assumed from RLS alone).
  [Y] Teardown: zero leftover rows.

Pass/fail only. Prints a final ``TOTAL: N PASS, M FAIL`` line and exits
non-zero on any failure.

Run:  python3 apps/api/scripts/verify_tiergating.py
"""
from __future__ import annotations

import asyncio
import inspect
import pathlib
import sys
from uuid import UUID

HERE = pathlib.Path(__file__).resolve()
sys.path.insert(0, str(HERE.parent))
from _db_bootstrap import bootstrap_async  # noqa: E402  (also puts apps/api on sys.path)

import asyncpg  # noqa: E402

from services.action_registry import REGISTRY, AssistantAction  # noqa: E402
from services.assistant_actions import register_all  # noqa: E402
from services import agent_proposals, workflow_scheduler, workflow_todos  # noqa: E402
import services.workflow_engine as eng  # noqa: E402
from services.workflow_engine import (  # noqa: E402
    WorkflowEngineError,
    compute_effective_tier,
    resolve_tier_approval,
    start_workflow_run,
)
from services.workflow_steps_deriver import derive_and_store_steps  # noqa: E402

# ═══════════════════════════════════════════════════════════════════════════
# Constants / fixtures — the "a7" block is unused by any other verify script
# (checked by grep across scripts/*.py before picking it).
# ═══════════════════════════════════════════════════════════════════════════
ORG_A = UUID("99000000-0000-0000-0000-0000a70a0001")   # fixture org — maker + 2 reviewers
ORG_B = UUID("99000000-0000-0000-0000-0000a70b0001")   # fixture org — cross-org reviewer only

U_MAKER = UUID("99000000-0000-0000-0000-0000a70a0011")     # ORG_A: starts runs, ALSO holds review_agent_proposals
U_REVIEWER1 = UUID("99000000-0000-0000-0000-0000a70a0012")  # ORG_A: the "other eligible checker"
U_TRIGGER_CREATOR = UUID("99000000-0000-0000-0000-0000a70a0013")  # ORG_A: simulates a workflow_triggers.created_by
U_REVIEWER_B = UUID("99000000-0000-0000-0000-0000a70b0011")  # ORG_B: cross-org reviewer

ALL_FIXTURE_USERS = [U_MAKER, U_REVIEWER1, U_TRIGGER_CREATOR, U_REVIEWER_B]
ALL_FIXTURE_ORGS = [ORG_A, ORG_B]

NAME_MARKER = "TIERGATING_VERIFY"
ROLE_REVIEWER_A = "TIERGATING Verify Reviewer A"
ROLE_REVIEWER_B = "TIERGATING Verify Reviewer B"
PROFILE_VIEWER_A = "TIERGATING Verify Run Viewer"

HEADERS = {"Authorization": "Bearer verify-token"}

# Fake, hermetic action-registry verbs — no real side effect beyond
# incrementing CALL_COUNTS. Each scenario gets its own key so counts never
# cross-contaminate between scenarios run in the same process.
KEY_DOWNGRADE = "tiergating.verify_downgrade"          # registry tier 1
KEY_UPGRADE = "tiergating.verify_upgrade"              # registry tier 3
KEY_REGRESSION = "tiergating.verify_regression"        # registry tier 3
KEY_NULLMAKER = "tiergating.verify_null_maker"         # registry tier 1
KEY_SCHEDMAKER = "tiergating.verify_scheduled_maker"   # registry tier 1

FAKE_ACTION_TIERS = {
    KEY_DOWNGRADE: 1,
    KEY_UPGRADE: 3,
    KEY_REGRESSION: 3,
    KEY_NULLMAKER: 1,
    KEY_SCHEDMAKER: 1,
}

# Definitions / versions — one pair per scenario.
DEF_DOWNGRADE = UUID("99000000-0000-0000-0000-0000a7d10001")
VER_DOWNGRADE = UUID("99000000-0000-0000-0000-0000a7d10002")
DEF_UPGRADE = UUID("99000000-0000-0000-0000-0000a7d20001")
VER_UPGRADE = UUID("99000000-0000-0000-0000-0000a7d20002")
DEF_REGRESSION = UUID("99000000-0000-0000-0000-0000a7d30001")
VER_REGRESSION = UUID("99000000-0000-0000-0000-0000a7d30002")
DEF_NULLMAKER = UUID("99000000-0000-0000-0000-0000a7d40001")
VER_NULLMAKER = UUID("99000000-0000-0000-0000-0000a7d40002")
DEF_SCHEDMAKER = UUID("99000000-0000-0000-0000-0000a7d50001")
VER_SCHEDMAKER = UUID("99000000-0000-0000-0000-0000a7d50002")
ALL_DEF_IDS = [DEF_DOWNGRADE, DEF_UPGRADE, DEF_REGRESSION, DEF_NULLMAKER, DEF_SCHEDMAKER]

BPMN_NS = "http://www.omg.org/spec/BPMN/20100524/MODEL"
EXT_NS = "http://2ndactcapital.com/bpmn/ext"

_ok = True
_n_pass = 0
_n_fail = 0
_finds: list[str] = []


def check(passed: bool, label: str, detail: str = "") -> bool:
    """Record one assertion. MUST be called (passed, label, detail) — the
    previous sprint's helper (scripttaskrefusal's sibling scripts had it
    right, but the instruction is explicit this time) had its arguments
    reversed once, and every assertion reported PASS regardless of outcome.
    The isinstance assertion below is the guard against that ever slipping
    through silently again: a non-bool `passed` fails LOUD, at the call
    site, not quietly as an always-true label string.
    """
    assert isinstance(passed, bool), (
        f"check() received passed={passed!r} (type {type(passed).__name__}), "
        f"not a bool, for label={label!r} — this is exactly the reversed-"
        "argument bug this assertion exists to catch"
    )
    global _ok, _n_pass, _n_fail
    mark = "[PASS]" if passed else "[FAIL]"
    line = f"{mark} {label}"
    if detail:
        line += f"  — {detail}"
    print(line)
    if passed:
        _n_pass += 1
    else:
        _n_fail += 1
        _ok = False
    return passed


def find(label: str, detail: str = "") -> None:
    print(f"[FIND] {label}" + (f"  — {detail}" if detail else ""))
    _finds.append(label)


# ═══════════════════════════════════════════════════════════════════════════
# Fake action registry verbs
# ═══════════════════════════════════════════════════════════════════════════
CALL_COUNTS: dict[str, int] = {}


def _make_handler(key: str):
    async def _handler(*, pool, user_id, org_id, run_context, workflow_run_id, **_):
        CALL_COUNTS[key] = CALL_COUNTS.get(key, 0) + 1
        return {"data": {"called_for": key, "count": CALL_COUNTS[key]}, "text": None}
    return _handler


def register_fake_actions() -> None:
    for key, tier in FAKE_ACTION_TIERS.items():
        REGISTRY.register(AssistantAction(
            key=key,
            module="tiergating_verify",
            description="tiergating.structural verify fixture — increments "
                         "CALL_COUNTS, no real side effect",
            access_type="write",
            required_permission=None,
            tier=tier,
            reversible=False,
            render_target="inline",
            handler=_make_handler(key),
            workflow_invocable=True,
        ))


# ═══════════════════════════════════════════════════════════════════════════
# BPMN builder — one Service Task, no User Task, so a non-suspending run
# reaches 'completed' with nothing else to drive.
# ═══════════════════════════════════════════════════════════════════════════
def _single_service_bpmn(suffix: str, action_key: str, diagram_tier: int) -> str:
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<bpmn:definitions xmlns:bpmn="{BPMN_NS}" xmlns:twoa="{EXT_NS}" '
        f'id="D_{suffix}" targetNamespace="http://2ndactcapital.com/bpmn">'
        f'<bpmn:process id="proc_{suffix}" isExecutable="true">'
        '<bpmn:startEvent id="Start_1"><bpmn:outgoing>f1</bpmn:outgoing></bpmn:startEvent>'
        '<bpmn:serviceTask id="Svc_1" name="Fake verb">'
        f'<bpmn:extensionElements><twoa:governance actionRegistryKey="{action_key}" '
        f'autonomyTier="{diagram_tier}"/></bpmn:extensionElements>'
        '<bpmn:incoming>f1</bpmn:incoming><bpmn:outgoing>f2</bpmn:outgoing></bpmn:serviceTask>'
        '<bpmn:endEvent id="End_1"><bpmn:incoming>f2</bpmn:incoming></bpmn:endEvent>'
        '<bpmn:sequenceFlow id="f1" sourceRef="Start_1" targetRef="Svc_1"/>'
        '<bpmn:sequenceFlow id="f2" sourceRef="Svc_1" targetRef="End_1"/>'
        '</bpmn:process></bpmn:definitions>'
    )


# ═══════════════════════════════════════════════════════════════════════════
# Teardown / seed — raw connection, explicit is_super_admin GUC per
# transaction (CLAUDE.md's platform_scope() convention). Ordering matters:
# workflow_run_steps (references agent_proposals via the NEW
# agent_proposal_id column, plus workflow_runs/workflow_steps) before
# workflow_runs/workflow_steps/workflow_versions/workflow_definitions,
# before agent_proposals, before member_todos/audit_log (both carry org_id
# directly, deleted by org rather than by user so an undelivered-alert row
# with a NULL user_id is still caught), before user_roles/role_permissions/
# roles, before profiles/profile_permissions, before users, before
# organizations.
# ═══════════════════════════════════════════════════════════════════════════
async def _super(conn) -> None:
    await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")


async def _read_rls_row(conn, query, *args):
    async with conn.transaction():
        await _super(conn)
        return await conn.fetchrow(query, *args)


async def _read_rls_val(conn, query, *args):
    async with conn.transaction():
        await _super(conn)
        return await conn.fetchval(query, *args)


async def _read_rls(conn, query, *args):
    async with conn.transaction():
        await _super(conn)
        return await conn.fetch(query, *args)


async def teardown(conn) -> None:
    async with conn.transaction():
        await _super(conn)
        stray_ids = [
            r["id"] for r in await conn.fetch(
                "SELECT id FROM workflow_definitions WHERE name LIKE $1 OR description LIKE $1",
                f"%{NAME_MARKER}%",
            )
        ]
        all_ids = list({*ALL_DEF_IDS, *stray_ids})
        await conn.execute(
            """DELETE FROM workflow_run_steps WHERE workflow_run_id IN (
                 SELECT r.id FROM workflow_runs r
                 JOIN workflow_versions v ON v.id = r.workflow_version_id
                 WHERE v.workflow_definition_id = ANY($1::uuid[]))""",
            all_ids,
        )
        await conn.execute(
            """DELETE FROM workflow_runs WHERE workflow_version_id IN (
                 SELECT id FROM workflow_versions
                 WHERE workflow_definition_id = ANY($1::uuid[]))""",
            all_ids,
        )
        await conn.execute(
            """DELETE FROM workflow_steps WHERE workflow_version_id IN (
                 SELECT id FROM workflow_versions
                 WHERE workflow_definition_id = ANY($1::uuid[]))""",
            all_ids,
        )
        await conn.execute(
            "DELETE FROM workflow_versions WHERE workflow_definition_id = ANY($1::uuid[])",
            all_ids,
        )
        await conn.execute(
            "DELETE FROM workflow_definitions WHERE id = ANY($1::uuid[])", all_ids
        )
        await conn.execute(
            "DELETE FROM agent_proposals WHERE org_id = ANY($1::uuid[])", ALL_FIXTURE_ORGS
        )
        await conn.execute(
            "DELETE FROM member_todos WHERE org_id = ANY($1::uuid[])", ALL_FIXTURE_ORGS
        )
        await conn.execute(
            "DELETE FROM audit_log WHERE org_id = ANY($1::uuid[])", ALL_FIXTURE_ORGS
        )
        await conn.execute(
            """DELETE FROM user_roles WHERE role_id IN
                 (SELECT id FROM roles WHERE org_id = ANY($1::uuid[]) AND name LIKE $2)""",
            ALL_FIXTURE_ORGS, f"%{NAME_MARKER.split('_')[0]}%",
        )
        await conn.execute(
            """DELETE FROM role_permissions WHERE role_id IN
                 (SELECT id FROM roles WHERE org_id = ANY($1::uuid[]) AND name IN ($2, $3))""",
            ALL_FIXTURE_ORGS, ROLE_REVIEWER_A, ROLE_REVIEWER_B,
        )
        await conn.execute(
            "DELETE FROM roles WHERE org_id = ANY($1::uuid[]) AND name IN ($2, $3)",
            ALL_FIXTURE_ORGS, ROLE_REVIEWER_A, ROLE_REVIEWER_B,
        )
        await conn.execute("UPDATE users SET profile_id = NULL WHERE id = ANY($1::uuid[])", ALL_FIXTURE_USERS)
        await conn.execute(
            """DELETE FROM profile_permissions WHERE profile_id IN
                 (SELECT id FROM profiles WHERE org_id = $1 AND name = $2)""",
            ORG_A, PROFILE_VIEWER_A,
        )
        await conn.execute(
            "DELETE FROM profiles WHERE org_id = $1 AND name = $2 AND is_seed = false",
            ORG_A, PROFILE_VIEWER_A,
        )
        await conn.execute("DELETE FROM users WHERE id = ANY($1::uuid[])", ALL_FIXTURE_USERS)
        await conn.execute("DELETE FROM organizations WHERE id = ANY($1::uuid[])", ALL_FIXTURE_ORGS)


def _sub(uid: UUID) -> str:
    return f"tiergating_{uid.hex}"


async def _grant_role(conn, *, org_id, role_name: str, user_id, permission_name: str) -> None:
    role_id = await conn.fetchval(
        """INSERT INTO roles (org_id, name, description) VALUES ($1, $2, $3)
           ON CONFLICT (org_id, name) DO UPDATE SET description = EXCLUDED.description
           RETURNING id""",
        org_id, role_name, f"{NAME_MARKER} fixture role",
    )
    perm_id = await conn.fetchval("SELECT id FROM permissions WHERE name = $1", permission_name)
    if perm_id is None:
        raise RuntimeError(
            f"permission {permission_name!r} does not exist — "
            "agenticmakerchecker_substrate.sql may not be applied"
        )
    await conn.execute(
        "INSERT INTO role_permissions (role_id, permission_id) VALUES ($1, $2) "
        "ON CONFLICT DO NOTHING",
        role_id, perm_id,
    )
    await conn.execute(
        "INSERT INTO user_roles (user_id, role_id) VALUES ($1, $2) ON CONFLICT DO NOTHING",
        user_id, role_id,
    )


async def seed(conn) -> dict:
    """Returns {"viewer_profile": <uuid>}."""
    async with conn.transaction():
        await _super(conn)

        for org_id, name in ((ORG_A, "TIERGATING Fixture Org A"), (ORG_B, "TIERGATING Fixture Org B")):
            await conn.execute(
                """INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3)
                   ON CONFLICT (id) DO NOTHING""",
                org_id, name, str(org_id) + "-tiergating",
            )

        async def mk_user(uid, org_id):
            sub = _sub(uid)
            await conn.execute(
                """INSERT INTO users (id, org_id, email, full_name, auth0_sub, role, is_active)
                   VALUES ($1, $2, $3, $4, $5, 'member', true)
                   ON CONFLICT (auth0_sub) DO UPDATE
                     SET org_id = EXCLUDED.org_id, is_active = true""",
                uid, org_id, f"{sub}@test.local", sub, sub,
            )

        await mk_user(U_MAKER, ORG_A)
        await mk_user(U_REVIEWER1, ORG_A)
        await mk_user(U_TRIGGER_CREATOR, ORG_A)
        await mk_user(U_REVIEWER_B, ORG_B)

        # review_agent_proposals via the ROLE system — the maker holds it
        # too (needed so the maker-checker refusal below is a genuine
        # MakerCheckerError, not a plain "you don't hold the permission"
        # NotEligibleError).
        await _grant_role(
            conn, org_id=ORG_A, role_name=ROLE_REVIEWER_A, user_id=U_MAKER,
            permission_name=agent_proposals.REVIEW_PERMISSION,
        )
        await _grant_role(
            conn, org_id=ORG_A, role_name=ROLE_REVIEWER_A, user_id=U_REVIEWER1,
            permission_name=agent_proposals.REVIEW_PERMISSION,
        )
        await _grant_role(
            conn, org_id=ORG_B, role_name=ROLE_REVIEWER_B, user_id=U_REVIEWER_B,
            permission_name=agent_proposals.REVIEW_PERMISSION,
        )

        # view_workflow_runs via the PROFILE system, for the run-console
        # display proof (GET /admin/workflow-runs/{id} is gated Profile-side).
        viewer_profile = await conn.fetchval(
            """INSERT INTO profiles (org_id, name, description, is_seed)
               VALUES ($1, $2, 'tiergating verify', false)
               ON CONFLICT (org_id, name) DO UPDATE SET updated_at = now()
               RETURNING id""",
            ORG_A, PROFILE_VIEWER_A,
        )
        await conn.execute(
            """INSERT INTO profile_permissions (org_id, profile_id, permission_key)
               VALUES ($1, $2, 'view_workflow_runs') ON CONFLICT (profile_id, permission_key) DO NOTHING""",
            ORG_A, viewer_profile,
        )
        await conn.execute("UPDATE users SET profile_id = $2 WHERE id = $1", U_MAKER, viewer_profile)

        # One definition/version/steps trio per scenario.
        specs = (
            (DEF_DOWNGRADE, VER_DOWNGRADE, "downgrade", KEY_DOWNGRADE, 3),   # registry 1, diagram 3
            (DEF_UPGRADE, VER_UPGRADE, "upgrade", KEY_UPGRADE, 1),           # registry 3, diagram 1
            (DEF_REGRESSION, VER_REGRESSION, "regression", KEY_REGRESSION, 3),  # registry 3, diagram 3
            (DEF_NULLMAKER, VER_NULLMAKER, "nullmaker", KEY_NULLMAKER, 1),   # registry 1, diagram 1
            (DEF_SCHEDMAKER, VER_SCHEDMAKER, "schedmaker", KEY_SCHEDMAKER, 1),  # registry 1, diagram 1
        )
        for def_id, ver_id, suffix, action_key, diagram_tier in specs:
            xml = _single_service_bpmn(suffix, action_key, diagram_tier)
            await conn.execute(
                """INSERT INTO workflow_definitions (id, org_id, name, description, created_by)
                   VALUES ($1, $2, $3, $4, $5) ON CONFLICT (id) DO NOTHING""",
                def_id, ORG_A, f"{NAME_MARKER} {suffix}", f"{NAME_MARKER} fixture", U_MAKER,
            )
            await conn.execute(
                """INSERT INTO workflow_versions
                     (id, workflow_definition_id, org_id, version_number, bpmn_xml,
                      change_summary, is_current, created_by)
                   VALUES ($1, $2, $3, 1, $4, 'tiergating fixture', true, $5)
                   ON CONFLICT (id) DO NOTHING""",
                ver_id, def_id, ORG_A, xml, U_MAKER,
            )
            await derive_and_store_steps(conn, ver_id, ORG_A, xml)

    return {"viewer_profile": viewer_profile}


# ═══════════════════════════════════════════════════════════════════════════
# Task 1 — discovery, reported live
# ═══════════════════════════════════════════════════════════════════════════
async def task1_report() -> None:
    print("\n── TASK 1 — discovery findings (live) ──")

    # 1a — the exact auto-complete point, and that resumption is free.
    drive_src = inspect.getsource(eng._drive)
    suspend_src = inspect.getsource(eng._suspend_step)
    check(
        "SUSPEND_TIER" in drive_src and "return executed, step_key" in drive_src,
        "[1a] _drive's Service-Task loop now branches on effective tier BEFORE "
        "calling _execute_service_task — a Tier-1 STARTED task is returned "
        "unexecuted (still parked) instead of being auto-completed unconditionally",
    )
    check(
        "serialize_state(workflow)" in suspend_src,
        "[1a] suspension serializes state via the SAME BpmnWorkflowSerializer "
        "path already used to pause at a User Task — a STARTED Service Task "
        "resumes exactly like a READY User Task; no new SpiffWorkflow "
        "machinery was needed for resumability",
    )
    find(
        "[1a] resumption needs nothing beyond what already exists: "
        "spiff_serialized_state + BpmnWorkflowSerializer already proved "
        "resumable-pause for User Tasks before this sprint; suspension reuses "
        "it verbatim for a STARTED Service Task.",
    )

    # 1b — what Tier 2 should do: nothing new.
    default_tier_src = inspect.getsource(
        __import__("services.workflow_steps_deriver", fromlist=["_default_tier"])._default_tier
    )
    find(
        "[1b] Tier 2 ('confirm & log') requires NO engine change. Every "
        "Service Task, at every tier, already writes a real result to "
        "workflow_run_steps.result (action_registry_key/resolved/invoked/"
        "handler_data) on every execution — that already IS the 'log' half. "
        "'Confirm' means a human CAN inspect the run afterward via the run "
        "console, not a real-time approval gate: the diagram editor's own "
        "label says 'confirm & log', and only Tier 1's label says 'approval "
        "required'. Tier 2 continues to auto-execute exactly like Tier 3 — "
        "this sprint's only new suspend behavior is at Tier 1.",
        f"_default_tier's write-capable-or-unresolved branch still returns 2, "
        f"unchanged: {'return 2' in default_tier_src}",
    )

    # 1c — workflow_triggers.created_by, and _fire already passes it through.
    fire_src = inspect.getsource(workflow_scheduler._fire)
    passes_created_by = (
        'trigger["created_by"]' in fire_src or "trigger['created_by']" in fire_src
    )
    check(
        passes_created_by,
        "[1c] services.workflow_scheduler._fire ALREADY passes "
        "trigger['created_by'] as start_workflow_run's started_by argument — "
        "the scheduled-run maker question this sprint's prompt raised as open "
        "does not hold against the live code; workflow_runs.started_by was "
        "already the correct maker for a scheduled run, same shape as a "
        "manual one",
        f"found in _fire source: {passes_created_by}",
    )

    # 1d — the run-status vocabulary gained 'awaiting_approval' / 'rejected'.
    from routers.workflows import RUN_STATUSES
    check(
        "awaiting_approval" in RUN_STATUSES and "rejected" in RUN_STATUSES,
        "[1d] routers.workflows.RUN_STATUSES now includes 'awaiting_approval' "
        "(did not exist before this sprint) and 'rejected' (new terminal "
        "state for a decided-against Tier-1 step)",
        f"RUN_STATUSES={RUN_STATUSES}",
    )


# ═══════════════════════════════════════════════════════════════════════════
async def main_async() -> int:
    from services.database import close_pool, get_pool, reset_rls_context, set_rls_context

    dsn = await bootstrap_async()
    if not dsn:
        print("[SKIP] no working DATABASE_URL — nothing can be proven")
        return 2

    register_all()
    register_fake_actions()
    CALL_COUNTS.clear()

    # main.py's FastAPI @app.on_event("startup") handler unconditionally
    # calls REGISTRY.sync_catalog(pool, seed_org_id) for the REAL 2nd Act
    # production org (00000000-0000-0000-0000-000000000001) — and it fires
    # on EVERY TestClient(main.app).__enter__() below (each entry replays the
    # full ASGI lifespan). sync_catalog iterates the WHOLE registry with no
    # filtering, so without this, every TestClient call in this script would
    # UPSERT the five "tiergating.verify_*" fixture actions into a REAL
    # org's REAL assistant_action_catalog table — exactly the kind of
    # verify-script-touches-production-data mistake CLAUDE.md warns about.
    # Hermetically no-op it for this process only (same technique other
    # verify scripts use to fake `anthropic`), and restore it in the outer
    # `finally` below regardless of outcome.
    _original_sync_catalog = REGISTRY.sync_catalog

    async def _hermetic_sync_catalog(pool, org_id):
        return None

    REGISTRY.sync_catalog = _hermetic_sync_catalog

    conn = await asyncpg.connect(dsn, statement_cache_size=0, ssl="require")
    try:
        await teardown(conn)
        await task1_report()

        print("\n── Fixtures ──")
        fixtures = await seed(conn)
        check(
            fixtures["viewer_profile"] is not None,
            "the ORG_A viewer profile (view_workflow_runs) was created",
            f"viewer_profile={fixtures['viewer_profile']}",
        )

        await close_pool()
        pool = await get_pool()

        # ── compute_effective_tier: direct, DB-independent proof of direction ──
        print("\n── Direct compute_effective_tier assertions (most-restrictive-wins) ──")
        t1_action = REGISTRY.get(KEY_DOWNGRADE)   # registry tier 1
        t3_action = REGISTRY.get(KEY_UPGRADE)     # registry tier 3
        check(
            compute_effective_tier({"autonomy_tier": 3}, t1_action) == 1,
            "a Tier-1 registry verb marked Tier 3 in the diagram computes "
            "effective tier 1 — the author cannot downgrade",
        )
        check(
            compute_effective_tier({"autonomy_tier": 1}, t3_action) == 1,
            "a Tier-3 registry verb marked Tier 1 in the diagram computes "
            "effective tier 1 — the author CAN upgrade",
        )
        check(
            compute_effective_tier({"autonomy_tier": 3}, t3_action) == 3,
            "a Tier-3 registry verb marked Tier 3 in the diagram computes "
            "effective tier 3 — agreement is not disturbed",
        )

        # ── DOWNGRADE: Tier-1 verb, diagram says Tier 3 — still suspends ──────
        print("\n── [Y] Tier-1 verb marked Tier 3 in the diagram STILL suspends ──")
        tokens = set_rls_context(ORG_A, False)
        try:
            run = await start_workflow_run(pool, VER_DOWNGRADE, ORG_A, {"note": "downgrade"}, U_MAKER)
        finally:
            reset_rls_context(tokens)
        check(
            run["status"] == "awaiting_approval" and run["suspended_at"] == "Svc_1",
            "the run suspends at the Service Task instead of completing, "
            "despite the diagram marking it Tier 3",
            f"run={run}",
        )
        check(
            CALL_COUNTS.get(KEY_DOWNGRADE, 0) == 0,
            "the verb's side effect GENUINELY did not happen — CALL_COUNTS has "
            "no entry for this key at all (not merely 'the run status "
            "changed')",
            f"CALL_COUNTS.get={CALL_COUNTS.get(KEY_DOWNGRADE)}",
        )
        downgrade_run_id = run["run_id"]
        downgrade_step = await _read_rls_row(
            conn,
            """SELECT rs.id, rs.status, rs.agent_proposal_id, ws.autonomy_tier,
                      ws.action_registry_key
               FROM workflow_run_steps rs JOIN workflow_steps ws ON ws.id = rs.workflow_step_id
               WHERE rs.workflow_run_id = $1""",
            downgrade_run_id,
        )
        check(
            downgrade_step is not None and downgrade_step["status"] == "suspended"
            and downgrade_step["agent_proposal_id"] is not None,
            "the run_step is 'suspended' and linked to a real agent_proposals row",
            f"downgrade_step={dict(downgrade_step) if downgrade_step else None}",
        )
        proposal_row = await _read_rls_row(
            conn,
            "SELECT proposed_by, status FROM agent_proposals WHERE id = $1",
            downgrade_step["agent_proposal_id"],
        )
        check(
            proposal_row is not None and str(proposal_row["proposed_by"]) == str(U_MAKER)
            and proposal_row["status"] == "pending",
            "the agent_proposals row's proposed_by is the run's own started_by "
            "(the maker), status pending",
            f"proposal_row={dict(proposal_row) if proposal_row else None}",
        )
        alert_recipients = await _read_rls(
            conn,
            "SELECT user_id FROM member_todos WHERE source = $1 AND related_id = $2",
            workflow_todos.TODO_SOURCE_TIER_APPROVAL,
            downgrade_step["id"],
        )
        alert_uids = {str(r["user_id"]) for r in alert_recipients}
        check(
            alert_uids == {str(U_MAKER), str(U_REVIEWER1)},
            "both review_agent_proposals holders in ORG_A (the maker AND "
            "reviewer1) were alerted — the recipient set matches EXACTLY who "
            "is eligible to act, never wider or narrower",
            f"alert_uids={alert_uids}",
        )

        # ── Run console shows the EFFECTIVE tier, not the diagram's ──────────
        print("\n── [Y] GET /admin/workflow-runs/{id} shows the EFFECTIVE tier ──")
        await close_pool()
        loop = asyncio.get_running_loop()
        status, body = await loop.run_in_executor(
            None, _api_get_run, U_MAKER, ORG_A, downgrade_run_id
        )
        await close_pool()
        pool = await get_pool()
        step_out = None
        if status == 200:
            step_out = next((s for s in body.get("steps", []) if s["id"] == str(downgrade_step["id"])), None)
        check(
            status == 200 and step_out is not None
            and step_out.get("effective_tier") == 1 and step_out.get("autonomy_tier") == 3,
            "the API reports effective_tier=1 (what the engine actually "
            "enforced) alongside autonomy_tier=3 (the diagram's raw, "
            "overridden value) — the console can no longer show a tier that "
            "disagrees with what actually happened",
            f"status={status}, step_out={step_out}",
        )
        check(
            status == 200 and body.get("permissions", {}).get("can_review") is True,
            "permissions.can_review is true for the maker, who holds "
            "review_agent_proposals via the ROLE system — proving the "
            "envelope reads the SAME permission system the engine enforces",
            f"permissions={body.get('permissions') if status == 200 else None}",
        )

        # ── Maker cannot approve their own step when another reviewer exists ──
        print("\n── [Y] Maker cannot approve their own suspended step (another reviewer exists) ──")
        tokens = set_rls_context(ORG_A, False)
        maker_self_approve_raised = None
        try:
            await resolve_tier_approval(
                pool, downgrade_step["id"], reviewed_by=U_MAKER,
                decision=agent_proposals.STATUS_APPROVED,
            )
        except agent_proposals.MakerCheckerError as exc:
            maker_self_approve_raised = exc
        finally:
            reset_rls_context(tokens)
        check(
            isinstance(maker_self_approve_raised, agent_proposals.MakerCheckerError),
            "resolve_tier_approval refuses the maker's own self-approval "
            "attempt with MakerCheckerError, because reviewer1 IS an eligible "
            "other checker",
            f"raised={maker_self_approve_raised}",
        )
        check(
            CALL_COUNTS.get(KEY_DOWNGRADE, 0) == 0,
            "the refused self-approval did not invoke the verb",
        )

        # Both TestClient calls below run the ASGI app on its own loop/thread
        # (run_in_executor) — the main-loop `pool` this coroutine holds must
        # be closed first (same discipline as the run-console GET above and
        # verify_scripttaskrefusal.py's Task 3), or asyncpg raises "Future
        # attached to a different loop" the moment the request handler tries
        # to acquire a connection.
        await close_pool()
        status, detail = await loop.run_in_executor(
            None, _api_decision, U_MAKER, ORG_A, downgrade_run_id, downgrade_step["id"], "approved",
        )
        check(
            status == 403,
            "the SAME refusal happens at the real HTTP boundary: "
            "POST .../decision as the maker returns 403",
            f"status={status} detail={str(detail)[:200]}",
        )

        # ── Cross-org: ORG_B's reviewer cannot reach ORG_A's suspended step ──
        print("\n── [Y] Cross-org: ORG_B's reviewer cannot reach ORG_A's suspended step ──")
        status, detail = await loop.run_in_executor(
            None, _api_decision, U_REVIEWER_B, ORG_B, downgrade_run_id, downgrade_step["id"], "approved",
        )
        check(
            status == 404,
            "a reviewer who genuinely holds review_agent_proposals — but in "
            "ORG_B, a DIFFERENT org from the suspended step's ORG_A — is "
            "refused with 404 (the router's own org-ownership check), not "
            "merely assumed safe because RLS exists",
            f"status={status} detail={str(detail)[:200]}",
        )
        check(
            CALL_COUNTS.get(KEY_DOWNGRADE, 0) == 0,
            "the cross-org attempt did not invoke the verb either",
        )
        # Close again (the TestClient requests may have created their own
        # pool bound to their own loop/thread) before restoring a pool bound
        # to THIS (main) loop for the service-layer calls below.
        await close_pool()
        pool = await get_pool()

        # ── Approval resumes and executes EXACTLY ONCE ────────────────────────
        print("\n── [Y] Approval by an eligible OTHER reviewer resumes and executes EXACTLY ONCE ──")
        tokens = set_rls_context(ORG_A, False)
        try:
            approve_result = await resolve_tier_approval(
                pool, downgrade_step["id"], reviewed_by=U_REVIEWER1,
                decision=agent_proposals.STATUS_APPROVED,
            )
        finally:
            reset_rls_context(tokens)
        check(
            approve_result["run_status"] == "completed",
            "the run resumes and completes after approval",
            f"approve_result={approve_result}",
        )
        check(
            CALL_COUNTS.get(KEY_DOWNGRADE) == 1,
            "the verb's handler was invoked EXACTLY ONCE — the count, not "
            "merely the absence of an error",
            f"CALL_COUNTS={CALL_COUNTS.get(KEY_DOWNGRADE)}",
        )
        run_row_after = await _read_rls_row(
            conn, "SELECT status FROM workflow_runs WHERE id = $1", downgrade_run_id
        )
        check(
            run_row_after is not None and run_row_after["status"] == "completed",
            "workflow_runs.status is genuinely 'completed' (re-read "
            "independently, not merely returned in-memory)",
            f"run_row_after={dict(run_row_after) if run_row_after else None}",
        )

        # ── A second approval attempt cannot double-fire ──────────────────────
        print("\n── [Y] A second approval attempt on the same (now completed) step is refused ──")
        tokens = set_rls_context(ORG_A, False)
        double_raised = None
        try:
            await resolve_tier_approval(
                pool, downgrade_step["id"], reviewed_by=U_REVIEWER1,
                decision=agent_proposals.STATUS_APPROVED,
            )
        except WorkflowEngineError as exc:
            double_raised = exc
        finally:
            reset_rls_context(tokens)
        check(
            isinstance(double_raised, WorkflowEngineError)
            and "not awaiting Tier-1 approval" in str(double_raised)
            and "completed" in str(double_raised),
            "resolve_tier_approval refuses outright because the step's own "
            "status is now 'completed', not 'suspended' — there is no path "
            "by which a second approval attempt could fire the verb again",
            f"raised={double_raised}",
        )
        check(
            CALL_COUNTS.get(KEY_DOWNGRADE) == 1,
            "the call count is STILL exactly 1 after the refused re-approval "
            "attempt",
            f"CALL_COUNTS={CALL_COUNTS.get(KEY_DOWNGRADE)}",
        )

        # ── UPGRADE: Tier-3 verb, diagram says Tier 1 — suspends; then REJECT ──
        print("\n── [Y] Tier-3 verb marked Tier 1 in the diagram DOES suspend; rejection means it never executes ──")
        tokens = set_rls_context(ORG_A, False)
        try:
            up_run = await start_workflow_run(pool, VER_UPGRADE, ORG_A, {}, U_MAKER)
        finally:
            reset_rls_context(tokens)
        check(
            up_run["status"] == "awaiting_approval",
            "the author's upgrade (marking a Tier-3 verb as Tier 1 in the "
            "diagram) genuinely suspends the run",
            f"up_run={up_run}",
        )
        up_step = await _read_rls_row(
            conn,
            """SELECT rs.id, rs.status, rs.agent_proposal_id
               FROM workflow_run_steps rs JOIN workflow_steps ws ON ws.id = rs.workflow_step_id
               WHERE rs.workflow_run_id = $1""",
            up_run["run_id"],
        )
        tokens = set_rls_context(ORG_A, False)
        try:
            reject_result = await resolve_tier_approval(
                pool, up_step["id"], reviewed_by=U_REVIEWER1,
                decision=agent_proposals.STATUS_REJECTED,
            )
        finally:
            reset_rls_context(tokens)
        check(
            reject_result["run_status"] == "rejected" and reject_result["executed"] is False,
            "rejection ends the run in the terminal 'rejected' state and "
            "reports executed=False",
            f"reject_result={reject_result}",
        )
        check(
            CALL_COUNTS.get(KEY_UPGRADE, 0) == 0,
            "the rejected verb's side effect GENUINELY never happened — no "
            "CALL_COUNTS entry at all",
            f"CALL_COUNTS.get={CALL_COUNTS.get(KEY_UPGRADE)}",
        )
        up_run_after = await _read_rls_row(
            conn, "SELECT status FROM workflow_runs WHERE id = $1", up_run["run_id"]
        )
        check(
            up_run_after is not None and up_run_after["status"] == "rejected",
            "workflow_runs.status is genuinely 'rejected' (re-read "
            "independently)",
            f"up_run_after={dict(up_run_after) if up_run_after else None}",
        )

        # ── REGRESSION: Tier-3 verb, diagram Tier 3 — executes immediately ────
        print("\n── [Y] A genuine Tier-3 step still executes immediately — no regression ──")
        tokens = set_rls_context(ORG_A, False)
        try:
            reg_run = await start_workflow_run(pool, VER_REGRESSION, ORG_A, {}, U_MAKER)
        finally:
            reset_rls_context(tokens)
        check(
            reg_run["status"] == "completed" and reg_run["suspended_at"] is None,
            "a Tier-3 Service Task runs to completion in the SAME call — no "
            "suspension, no approval step, exactly the pre-sprint behavior",
            f"reg_run={reg_run}",
        )
        check(
            CALL_COUNTS.get(KEY_REGRESSION) == 1,
            "the Tier-3 verb's handler WAS invoked, exactly once, immediately",
            f"CALL_COUNTS={CALL_COUNTS.get(KEY_REGRESSION)}",
        )

        # ── NULL maker: never "anyone may approve" ────────────────────────────
        print("\n── [Y] A run with no resolvable maker is refused loudly, never silently 'anyone may approve' ──")
        tokens = set_rls_context(ORG_A, False)
        null_maker_raised = None
        try:
            await start_workflow_run(pool, VER_NULLMAKER, ORG_A, {}, None)
        except WorkflowEngineError as exc:
            null_maker_raised = exc
        finally:
            reset_rls_context(tokens)
        check(
            isinstance(null_maker_raised, WorkflowEngineError) and "maker" in str(null_maker_raised),
            "start_workflow_run with started_by=None raises WorkflowEngineError "
            "naming the maker requirement, rather than silently suspending "
            "with an approvable-by-anyone proposal",
            f"raised={null_maker_raised}",
        )
        check(
            CALL_COUNTS.get(KEY_NULLMAKER, 0) == 0,
            "the verb never ran",
        )
        null_run_row = await _read_rls_row(
            conn,
            """SELECT r.status FROM workflow_runs r JOIN workflow_versions v ON v.id = r.workflow_version_id
               WHERE v.workflow_definition_id = $1 ORDER BY r.started_at DESC LIMIT 1""",
            DEF_NULLMAKER,
        )
        check(
            null_run_row is not None and null_run_row["status"] == "held",
            "the run is loudly HELD (not silently stuck, not silently "
            "suspended) — a real, findable state an operator will see",
            f"null_run_row={dict(null_run_row) if null_run_row else None}",
        )
        null_proposal_count = await _read_rls_val(
            conn,
            """SELECT count(*) FROM agent_proposals ap
               JOIN workflow_run_steps rs ON rs.agent_proposal_id = ap.id
               JOIN workflow_runs r ON r.id = rs.workflow_run_id
               JOIN workflow_versions v ON v.id = r.workflow_version_id
               WHERE v.workflow_definition_id = $1""",
            DEF_NULLMAKER,
        )
        check(
            null_proposal_count == 0,
            "zero agent_proposals rows were created for the null-maker run — "
            "there is genuinely nothing for 'anyone' to approve",
            f"null_proposal_count={null_proposal_count}",
        )

        # ── Scheduled-run maker resolution (Task 1c) ─────────────────────────
        print("\n── [Y] A scheduled run's maker resolves to the trigger's creator, never NULL ──")
        tokens = set_rls_context(ORG_A, False)
        try:
            sched_run = await start_workflow_run(
                pool, VER_SCHEDMAKER, ORG_A,
                {"trigger_id": "verify-fixture", "trigger_type": "scheduled"},
                U_TRIGGER_CREATOR,
            )
        finally:
            reset_rls_context(tokens)
        check(
            sched_run["status"] == "awaiting_approval",
            "the simulated scheduled run (started_by=the trigger's own "
            "created_by, exactly as workflow_scheduler._fire does it) "
            "suspends normally",
            f"sched_run={sched_run}",
        )
        sched_step = await _read_rls_row(
            conn,
            """SELECT rs.agent_proposal_id FROM workflow_run_steps rs
               WHERE rs.workflow_run_id = $1""",
            sched_run["run_id"],
        )
        sched_proposal = await _read_rls_row(
            conn, "SELECT proposed_by FROM agent_proposals WHERE id = $1",
            sched_step["agent_proposal_id"],
        )
        check(
            sched_proposal is not None and str(sched_proposal["proposed_by"]) == str(U_TRIGGER_CREATOR),
            "the resulting agent_proposals row's maker is the trigger's own "
            "creator — never NULL, never 'anyone'",
            f"sched_proposal={dict(sched_proposal) if sched_proposal else None}",
        )

    finally:
        try:
            await teardown(conn)
            async with conn.transaction():
                await _super(conn)
                leftovers = await conn.fetchval(
                    """SELECT (SELECT count(*) FROM workflow_definitions WHERE id = ANY($1::uuid[])
                                                                          OR name LIKE $4)
                            + (SELECT count(*) FROM workflow_versions WHERE workflow_definition_id = ANY($1::uuid[]))
                            + (SELECT count(*) FROM workflow_steps WHERE workflow_version_id IN (
                                 SELECT id FROM workflow_versions WHERE workflow_definition_id = ANY($1::uuid[])))
                            + (SELECT count(*) FROM workflow_runs WHERE workflow_version_id IN (
                                 SELECT id FROM workflow_versions WHERE workflow_definition_id = ANY($1::uuid[])))
                            + (SELECT count(*) FROM agent_proposals WHERE org_id = ANY($2::uuid[]))
                            + (SELECT count(*) FROM member_todos WHERE org_id = ANY($2::uuid[]))
                            + (SELECT count(*) FROM audit_log WHERE org_id = ANY($2::uuid[]))
                            + (SELECT count(*) FROM user_roles WHERE user_id = ANY($3::uuid[]))
                            + (SELECT count(*) FROM roles WHERE org_id = ANY($2::uuid[])
                                                          AND name IN ($5, $6))
                            + (SELECT count(*) FROM profiles WHERE org_id = ANY($2::uuid[])
                                                             AND is_seed = false)
                            + (SELECT count(*) FROM users WHERE id = ANY($3::uuid[]))
                            + (SELECT count(*) FROM organizations WHERE id = ANY($2::uuid[]))""",
                    ALL_DEF_IDS, ALL_FIXTURE_ORGS, ALL_FIXTURE_USERS, f"%{NAME_MARKER}%",
                    ROLE_REVIEWER_A, ROLE_REVIEWER_B,
                )
            check(
                leftovers == 0,
                "TEARDOWN: zero leftover fixture rows across workflow_definitions/"
                "versions/steps/runs/run_steps, agent_proposals, member_todos, "
                "audit_log, user_roles/roles, profiles, users, organizations",
                f"found {leftovers}",
            )
        finally:
            REGISTRY.sync_catalog = _original_sync_catalog
            from services.database import close_pool as _close_pool
            await _close_pool()
            await conn.close()

    print(f"\n{'=' * 70}\nTOTAL: {_n_pass} PASS, {_n_fail} FAIL, {len(_finds)} FIND\n{'=' * 70}")
    return 0 if _ok else 1


# ═══════════════════════════════════════════════════════════════════════════
# TestClient — sync, driven off the running loop via run_in_executor (matches
# verify_scripttaskrefusal.py / verify_workflowpermsfix.py: entered as a
# context manager, ONE client per call, or the app's module-global pool ends
# up bound to a dead event loop).
# ═══════════════════════════════════════════════════════════════════════════
def _auth_as(uid: UUID, org_id: UUID):
    import main

    sub = _sub(uid)
    main.verify_token = lambda _t: {
        "sub": sub, "email": f"{sub}@test.local", "org_id": str(org_id),
    }


def _api_get_run(uid: UUID, org_id: UUID, run_id):
    import main
    from starlette.testclient import TestClient

    _auth_as(uid, org_id)
    client = TestClient(main.app, raise_server_exceptions=False)
    client.__enter__()
    try:
        res = client.get(f"/api/v1/admin/workflow-runs/{run_id}", headers=HEADERS)
        try:
            return res.status_code, res.json()
        except Exception:  # noqa: BLE001
            return res.status_code, {}
    finally:
        client.__exit__(None, None, None)


def _api_decision(uid: UUID, org_id: UUID, run_id, step_id, decision: str):
    import main
    from starlette.testclient import TestClient

    _auth_as(uid, org_id)
    client = TestClient(main.app, raise_server_exceptions=False)
    client.__enter__()
    try:
        res = client.post(
            f"/api/v1/admin/workflow-runs/{run_id}/steps/{step_id}/decision",
            headers=HEADERS,
            json={"decision": decision},
        )
        try:
            detail = res.json().get("detail", "")
        except Exception:  # noqa: BLE001
            detail = res.text
        return res.status_code, detail
    finally:
        client.__exit__(None, None, None)


if __name__ == "__main__":
    sys.exit(asyncio.run(main_async()))
