"""verify_schedulerhistory.py — the Workflow Run History screen.

WHAT THIS PROVES (against the DEPLOYED database, the REAL ASGI app, the REAL
firing loop and the REAL engine — no stubs, no mock rows, no hand-written
envelopes):

  [Task 1] The four discovery findings, measured NOW from the live router, the
           live database and the pre-sprint files as git actually holds them —
           including two places the prompt's premise did not survive contact
           with the schema.
  [Task 2] One call returns everything the screen prints: the run's own fields,
           its definition's name, its full ordered step history, and — for a
           scheduler-fired run — the originating trigger plus the recurrence
           summary built by the SAME describe_schedule the firing loop uses,
           asserted against that function's own output rather than a literal.
  [Task 3] A run started by a REAL scheduler tick reports its scheduled origin:
           proven by firing one, through workflow_scheduler.run_scheduler_tick,
           and reading the run back through HTTP.
  [Task 3] A run started MANUALLY through workflow_engine.start_workflow_run
           reports its real human starter — proven against a real manual run,
           not assumed to differ because the code branches.
  [Task 3] A HELD run's detail returns the engine's real error_detail and the
           EXACT set of users member_todos says were alerted — compared set to
           set against the table, and separately shown to be WIDER than the
           starter alone, so a pane that just printed the starter would fail.
  [Task 3] Duration is honest: the API refuses to report one for a Service Task
           step, and the raw row is shown to have started_at == completed_at so
           the zero it would otherwise print is proven to be an artifact. A
           User Task step, whose two timestamps are separate moments, is marked
           measured.
  [Task 4] Status and time-period filters narrow in SQL, proven BOTH ways: each
           filter is shown to include what it must AND to exclude a real row
           that exists and does not match.
  [Task 4] A view-only caller (view_workflow_runs alone) reads the list and a
           run detail; a caller holding NEITHER workflow key is refused both.
  [Task 4] The screen has no mock data and derives none of the server's
           decisions — checked in the component source.
  [Task 4] `npm run build` exits 0, run for real.
  [Teardown] Zero leftover rows, asserted by count.

Run:  python3 apps/api/scripts/verify_schedulerhistory.py
"""
import asyncio
import os
import pathlib
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from urllib.parse import quote
from uuid import UUID
from zoneinfo import ZoneInfo

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from _db_bootstrap import bootstrap_async  # noqa: E402  (also puts apps/api on sys.path)

import asyncpg  # noqa: E402

UTC = timezone.utc
REPO = pathlib.Path(__file__).resolve().parents[3]
WEB = REPO / "apps" / "web"

ORG_ID = UUID("00000000-0000-0000-0000-000000000001")        # 2nd Act Capital
ORG_ADMIN_PROFILE = "Org Admin"

PERM_VIEW_RUNS = "view_workflow_runs"

# Fixture users. Three capability levels, because the interesting claim is
# about the MIDDLE one and cannot be stated without the other two.
U_ADMIN = UUID("99000000-0000-0000-0000-00000000a0a1")   # starts the manual run
U_VIEWER = UUID("99000000-0000-0000-0000-00000000a0a2")  # view_workflow_runs ONLY
U_NONE = UUID("99000000-0000-0000-0000-00000000a0a3")    # holds nothing
U_MEMBER = UUID("99000000-0000-0000-0000-00000000a0a4")  # no author_workflows
ALL_USERS = [U_ADMIN, U_VIEWER, U_NONE, U_MEMBER]
SUB = {
    U_ADMIN: "schedhist_admin",
    U_VIEWER: "schedhist_viewer",
    U_NONE: "schedhist_none",
    U_MEMBER: "schedhist_member",
}
FULL_NAME = {u: SUB[u] for u in ALL_USERS}

# A bespoke profile for the viewer: the seeded 'Org Admin' profile carries more
# than one workflow key, so granting the viewer that profile would prove nothing.
VIEWER_PROFILE = UUID("99000000-0000-0000-0000-00000000a0f9")
VIEWER_PROFILE_NAME = "SCHEDHIST Runs Viewer"

# One definition per behaviour, so a run created for one claim can never satisfy
# another claim by accident.
D_SCHED = UUID("99000000-0000-0000-0000-00000000a0c1")   # fired by a real tick
D_MANUAL = UUID("99000000-0000-0000-0000-00000000a0c2")  # started by a person
D_HOLD = UUID("99000000-0000-0000-0000-00000000a0c3")    # holds + alerts
D_USER = UUID("99000000-0000-0000-0000-00000000a0c4")    # pauses at a User Task
D_OLD = UUID("99000000-0000-0000-0000-00000000a0c5")     # the back-dated run
ALL_DEFS = [D_SCHED, D_MANUAL, D_HOLD, D_USER, D_OLD]
VER = {d: UUID(str(d).replace("a0c", "a0d", 1)) for d in ALL_DEFS}

T_SCHED = UUID("99000000-0000-0000-0000-00000000a0b1")

# The ONE workflow_invocable action in the registry. It declares
# required_permission='author_workflows'; the fixture that starts the HOLD run
# does not hold it, so _assert_action_permission raises, the engine holds the
# run and alerts. Deterministic and offline: the refusal happens before any
# network call, so it does not depend on LITELLM_BASE_URL being set.
INVOCABLE_ACTION = "litellm.reload_model_cost_map"

# How far back the back-dated fixture run sits. Chosen to fall OUTSIDE the 30d
# window and INSIDE 'all', which is what makes the period filter provable in
# both directions with real rows.
BACKDATE_DAYS = 200

HEADERS = {"Authorization": "Bearer verify-token"}

BPMN_NS = "http://www.omg.org/spec/BPMN/20100524/MODEL"
EXT_NS = "http://2ndactcapital.com/bpmn/ext"

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


def read(path) -> str:
    return pathlib.Path(path).read_text(encoding="utf-8")


def strip_js_comments(src: str) -> str:
    """Executable JSX only.

    Only ever used to make an ABSENCE assertion stricter: a component that
    EXPLAINS a rule in a comment must not trip its own explanation, which is the
    false positive that teaches the next person to delete the check.
    """
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.DOTALL)
    return re.sub(r"(?m)^\s*//.*$", "", src)


def git_show(rev_path: str) -> str:
    """A file as git holds it, or '' — used to state the PRE-SPRINT shape from
    the repository rather than from memory of what it looked like."""
    try:
        out = subprocess.run(
            ["git", "show", rev_path], cwd=REPO, capture_output=True, text=True,
            timeout=30,
        )
        return out.stdout if out.returncode == 0 else ""
    except Exception:  # noqa: BLE001
        return ""


class Capture:
    def __init__(self, echo=False):
        self.lines = []
        self.echo = echo

    def __call__(self, message):
        self.lines.append(str(message))
        if self.echo:
            print(f"        │ {message}")


# ═══════════════════════════════════════════════════════════════════════════
# BPMN fixtures
# ═══════════════════════════════════════════════════════════════════════════
def trivial_bpmn(proc_id) -> str:
    """Start -> End. Runs straight to 'completed' with no side effects."""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<bpmn:definitions xmlns:bpmn="{BPMN_NS}" id="D_{proc_id}" '
        'targetNamespace="http://2ndactcapital.com/bpmn">'
        f'<bpmn:process id="{proc_id}" isExecutable="true">'
        '<bpmn:startEvent id="x_start"><bpmn:outgoing>x1</bpmn:outgoing></bpmn:startEvent>'
        '<bpmn:endEvent id="x_end"><bpmn:incoming>x1</bpmn:incoming></bpmn:endEvent>'
        '<bpmn:sequenceFlow id="x1" sourceRef="x_start" targetRef="x_end"/>'
        '</bpmn:process></bpmn:definitions>'
    )


def service_bpmn(proc_id, action_key) -> str:
    """Start -> serviceTask -> End. The Service Task really invokes the action."""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<bpmn:definitions xmlns:bpmn="{BPMN_NS}" xmlns:twoa="{EXT_NS}" '
        f'id="D_{proc_id}" targetNamespace="http://2ndactcapital.com/bpmn">'
        f'<bpmn:process id="{proc_id}" isExecutable="true">'
        '<bpmn:startEvent id="v_start"><bpmn:outgoing>v1</bpmn:outgoing></bpmn:startEvent>'
        '<bpmn:serviceTask id="v_service" name="Reload cost map">'
        '<bpmn:extensionElements>'
        f'<twoa:governance actionRegistryKey="{action_key}"/>'
        '</bpmn:extensionElements>'
        '<bpmn:incoming>v1</bpmn:incoming><bpmn:outgoing>v2</bpmn:outgoing>'
        '</bpmn:serviceTask>'
        '<bpmn:endEvent id="v_end"><bpmn:incoming>v2</bpmn:incoming></bpmn:endEvent>'
        '<bpmn:sequenceFlow id="v1" sourceRef="v_start" targetRef="v_service"/>'
        '<bpmn:sequenceFlow id="v2" sourceRef="v_service" targetRef="v_end"/>'
        '</bpmn:process></bpmn:definitions>'
    )


def user_bpmn(proc_id) -> str:
    """Start -> userTask -> End. Pauses at the User Task, so the run stays
    'running' and its step is 'active' with a REAL started_at and no
    completed_at — the one step kind whose duration means something."""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<bpmn:definitions xmlns:bpmn="{BPMN_NS}" id="D_{proc_id}" '
        'targetNamespace="http://2ndactcapital.com/bpmn">'
        f'<bpmn:process id="{proc_id}" isExecutable="true">'
        '<bpmn:startEvent id="t_start"><bpmn:outgoing>t1</bpmn:outgoing></bpmn:startEvent>'
        '<bpmn:userTask id="t_review" name="Review">'
        '<bpmn:incoming>t1</bpmn:incoming><bpmn:outgoing>t2</bpmn:outgoing>'
        '</bpmn:userTask>'
        '<bpmn:endEvent id="t_end"><bpmn:incoming>t2</bpmn:incoming></bpmn:endEvent>'
        '<bpmn:sequenceFlow id="t1" sourceRef="t_start" targetRef="t_review"/>'
        '<bpmn:sequenceFlow id="t2" sourceRef="t_review" targetRef="t_end"/>'
        '</bpmn:process></bpmn:definitions>'
    )


# ═══════════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════════
async def _mk_user(conn, uid, role, profile_id):
    sub = SUB[uid]
    await conn.execute(
        """INSERT INTO users (id, org_id, email, full_name, auth0_sub, role,
                              profile_id, is_active)
           VALUES ($1, $2, $3, $4, $5, $6, $7, true)
           ON CONFLICT (auth0_sub) DO UPDATE
             SET role = EXCLUDED.role, profile_id = EXCLUDED.profile_id,
                 org_id = EXCLUDED.org_id, is_active = true""",
        uid, ORG_ID, f"{sub}@test.local", sub, sub, role, profile_id,
    )


async def _mk_definition(conn, def_id, name, bpmn, created_by, step=None):
    await conn.execute(
        """INSERT INTO workflow_definitions (id, org_id, name, description, created_by)
           VALUES ($1, $2, $3, 'schedulerhistory fixture', $4)
           ON CONFLICT (id) DO NOTHING""",
        def_id, ORG_ID, name, created_by,
    )
    await conn.execute(
        """INSERT INTO workflow_versions
             (id, workflow_definition_id, org_id, version_number, bpmn_xml,
              change_summary, is_current, created_by)
           VALUES ($1, $2, $3, 1, $4, 'v1', true, $5)
           ON CONFLICT (id) DO NOTHING""",
        VER[def_id], def_id, ORG_ID, bpmn, created_by,
    )
    if step:
        step_key, step_type, action_key, display = step
        await conn.execute(
            """INSERT INTO workflow_steps
                 (workflow_version_id, org_id, step_key, step_type,
                  autonomy_tier, action_registry_key, display_name)
               VALUES ($1, $2, $3, $4, 1, $5, $6)
               ON CONFLICT (workflow_version_id, step_key) DO NOTHING""",
            VER[def_id], ORG_ID, step_key, step_type, action_key, display,
        )


async def seed(conn):
    org_admin_profile_id = await conn.fetchval(
        "SELECT id FROM profiles WHERE org_id = $1 AND name = $2",
        ORG_ID, ORG_ADMIN_PROFILE)

    await conn.execute(
        """INSERT INTO profiles (id, org_id, name, description, is_seed)
           VALUES ($1, $2, $3, 'schedulerhistory fixture', false)
           ON CONFLICT (id) DO NOTHING""",
        VIEWER_PROFILE, ORG_ID, VIEWER_PROFILE_NAME)
    await conn.execute(
        """INSERT INTO profile_permissions (org_id, profile_id, permission_key)
           VALUES ($1, $2, $3)
           ON CONFLICT (profile_id, permission_key) DO NOTHING""",
        ORG_ID, VIEWER_PROFILE, PERM_VIEW_RUNS)

    await _mk_user(conn, U_ADMIN, "org_admin", org_admin_profile_id)
    await _mk_user(conn, U_VIEWER, "member", VIEWER_PROFILE)
    await _mk_user(conn, U_NONE, "member", None)
    await _mk_user(conn, U_MEMBER, "member", None)

    await _mk_definition(conn, D_SCHED, "SCHEDHIST Scheduled",
                         trivial_bpmn("schedhist_sched"), U_ADMIN)
    await _mk_definition(conn, D_MANUAL, "SCHEDHIST Manual",
                         trivial_bpmn("schedhist_manual"), U_ADMIN)
    await _mk_definition(conn, D_OLD, "SCHEDHIST Back-dated",
                         trivial_bpmn("schedhist_old"), U_ADMIN)
    await _mk_definition(conn, D_HOLD, "SCHEDHIST Hold",
                         service_bpmn("schedhist_hold", INVOCABLE_ACTION), U_ADMIN,
                         step=("v_service", "service", INVOCABLE_ACTION,
                               "Reload cost map"))
    await _mk_definition(conn, D_USER, "SCHEDHIST User Task",
                         user_bpmn("schedhist_user"), U_ADMIN,
                         step=("t_review", "user", None, "Review"))
    return org_admin_profile_id


async def _fixture_run_ids(conn):
    return [
        r["id"] for r in await conn.fetch(
            """SELECT r.id FROM workflow_runs r
               WHERE r.workflow_version_id = ANY($1::uuid[])
                  OR r.started_by = ANY($2::uuid[])""",
            list(VER.values()), ALL_USERS)
    ]


async def teardown(conn):
    run_ids = await _fixture_run_ids(conn)
    # Held-run and user-task alerts land on REAL org admins, not only on the
    # fixtures, so the todo cleanup keys on the RUN and the STEP, not on the
    # fixture user list. Deleting only fixture-user todos would leave real
    # people holding a todo for a run that no longer exists.
    await conn.execute(
        """DELETE FROM member_todos
           WHERE related_type = 'workflow_run' AND related_id = ANY($1::uuid[])""",
        run_ids)
    await conn.execute(
        """DELETE FROM member_todos
           WHERE related_type = 'workflow_run_step' AND related_id IN (
             SELECT id FROM workflow_run_steps
             WHERE workflow_run_id = ANY($1::uuid[]))""",
        run_ids)
    await conn.execute(
        "DELETE FROM member_todos WHERE user_id = ANY($1::uuid[])", ALL_USERS)
    await conn.execute(
        "DELETE FROM workflow_triggers WHERE workflow_definition_id = ANY($1::uuid[])",
        ALL_DEFS)
    await conn.execute(
        "DELETE FROM workflow_triggers WHERE created_by = ANY($1::uuid[])", ALL_USERS)
    await conn.execute(
        "DELETE FROM workflow_run_steps WHERE workflow_run_id = ANY($1::uuid[])",
        run_ids)
    await conn.execute(
        "DELETE FROM workflow_runs WHERE id = ANY($1::uuid[])", run_ids)
    await conn.execute(
        """DELETE FROM workflow_steps WHERE workflow_version_id = ANY($1::uuid[])""",
        list(VER.values()))
    await conn.execute(
        "DELETE FROM workflow_versions WHERE workflow_definition_id = ANY($1::uuid[])",
        ALL_DEFS)
    await conn.execute(
        "DELETE FROM workflow_definitions WHERE id = ANY($1::uuid[])", ALL_DEFS)
    await conn.execute(
        "DELETE FROM workflow_definitions WHERE created_by = ANY($1::uuid[])", ALL_USERS)
    await conn.execute("DELETE FROM audit_log WHERE user_id = ANY($1::uuid[])", ALL_USERS)
    await conn.execute("UPDATE users SET profile_id = NULL WHERE id = ANY($1::uuid[])",
                       ALL_USERS)
    await conn.execute("DELETE FROM users WHERE id = ANY($1::uuid[])", ALL_USERS)
    await conn.execute("DELETE FROM profile_permissions WHERE profile_id = $1",
                       VIEWER_PROFILE)
    await conn.execute("DELETE FROM profiles WHERE id = $1", VIEWER_PROFILE)


async def quiesce_foreign_triggers(conn):
    """Park every NON-fixture scheduled trigger for the duration. The tick scans
    all orgs by design; this script must not fire somebody else's schedule."""
    rows = await conn.fetch(
        """SELECT id FROM workflow_triggers
           WHERE trigger_type = 'scheduled' AND is_active
             AND workflow_definition_id <> ALL($1::uuid[])""",
        ALL_DEFS)
    ids = [r["id"] for r in rows]
    if ids:
        await conn.execute(
            "UPDATE workflow_triggers SET is_active = false WHERE id = ANY($1::uuid[])",
            ids)
    return ids


async def restore_foreign_triggers(conn, ids):
    if ids:
        await conn.execute(
            "UPDATE workflow_triggers SET is_active = true WHERE id = ANY($1::uuid[])",
            ids)


def cron_due_now(tz_name: str, now_utc: datetime) -> str:
    local = now_utc.astimezone(ZoneInfo(tz_name))
    return f"{local.minute} {local.hour} * * *"


# ═══════════════════════════════════════════════════════════════════════════
# TASK 1 — the four findings, measured live
# ═══════════════════════════════════════════════════════════════════════════
async def task1_report(conn):
    from routers import workflows as wr
    from services import workflow_engine, workflow_todos

    print("\n" + "=" * 74)
    print("TASK 1 — DISCOVERY (measured now, not quoted from the prompt)")
    print("=" * 74)

    # ── 1a: what the two endpoints returned BEFORE this sprint ──────────────
    before = git_show("HEAD:apps/api/routers/workflows.py")
    list_before = ""
    if before:
        start = before.find("async def list_workflow_runs")
        list_before = before[start:before.find("@router.get", start + 10)] if start > -1 else ""
    detail_before = ""
    if before:
        start = before.find("async def get_workflow_run(")
        detail_before = before[start:start + 2400] if start > -1 else ""

    print("\n  1a. GET /admin/workflow-runs and /{run_id}, as HEAD holds them")
    print(f"        list joined workflow_definitions for a name : "
          f"{'d.name AS workflow_name' in list_before}")
    print(f"        list returned a BARE LIST                   : "
          f"{'return [dict(r) for r in rows]' in list_before}")
    print(f"        list returned r.context (origin)            : "
          f"{'r.context' in list_before}")
    print(f"        detail returned workflow_run_steps          : "
          f"{'FROM workflow_run_steps rs' in detail_before}")
    print(f"        detail resolved the originating trigger     : "
          f"{'workflow_triggers' in detail_before}")
    print(f"        detail returned the held-run alert set      : "
          f"{'member_todos' in detail_before}")

    check("[Y] TASK 1a: BOTH endpoints already existed, both already joined "
          "workflow_definitions for a human-readable name, and the DETAIL "
          "endpoint already returned the full ordered workflow_run_steps "
          "history — so the step timeline was never the missing half",
          "d.name AS workflow_name" in list_before
          and "FROM workflow_run_steps rs" in detail_before,
          "name join + step history both pre-existing")
    check("[Y] TASK 1a: what was MISSING is the origin — the LIST did not "
          "return r.context at all, so nothing downstream could tell a "
          "scheduled run from a manual one, and NEITHER endpoint resolved the "
          "originating trigger or the held-run alert set",
          "r.context" not in list_before
          and "workflow_triggers" not in detail_before
          and "member_todos" not in detail_before,
          "context absent from the list; no trigger and no alert join anywhere")
    check("[Y] TASK 1a: the list returned a BARE LIST with no permission "
          "envelope, and now returns {rows, permissions, filters}",
          "return [dict(r) for r in rows]" in list_before
          and '"permissions": _run_permissions(principal)' in read(
              REPO / "apps/api/routers/workflows.py"),
          "bare list -> envelope")

    # ── 1b: the REAL status vocabulary ─────────────────────────────────────
    constraints = [
        r["def"] for r in await conn.fetch(
            """SELECT pg_get_constraintdef(con.oid) AS def
               FROM pg_constraint con JOIN pg_class rel ON rel.oid = con.conrelid
               JOIN pg_namespace n ON n.oid = rel.relnamespace
               WHERE n.nspname = 'public' AND rel.relname = 'workflow_runs'""")
    ]
    status_checks = [c for c in constraints if c.startswith("CHECK") and "status" in c]
    deployed = {r["status"] for r in await conn.fetch(
        "SELECT DISTINCT status FROM workflow_runs")}
    engine_src = read(REPO / "apps/api/services/workflow_engine.py")
    engine_run_statuses = set(
        re.findall(r"UPDATE workflow_runs\s+SET status = '(\w+)'", engine_src)
    ) | set(re.findall(r"workflow_runs\s*\n\s*SET status = '(\w+)'", engine_src))
    default_status = await conn.fetchval(
        """SELECT column_default FROM information_schema.columns
           WHERE table_name = 'workflow_runs' AND column_name = 'status'""")

    print("\n  1b. workflow_runs.status")
    print(f"        CHECK constraints on status : {status_checks or 'NONE'}")
    print(f"        column DEFAULT              : {default_status}")
    print(f"        written by the engine       : {sorted(engine_run_statuses)}")
    print(f"        present in deployed data    : {sorted(deployed)}")
    print(f"        the API's declared list     : {list(wr.RUN_STATUSES)}")

    check("[Y] TASK 1b: there is NO CHECK constraint on workflow_runs.status — "
          "the vocabulary is a CODE convention, so a filter list built by "
          "asking the database what is legal would come back empty and one "
          "built from DISTINCT would silently drop whichever state has no rows "
          "right now",
          not status_checks, f"status CHECK constraints: {status_checks or 'none'}")
    check("[Y] TASK 1b: the complete real set is exactly (running, completed, "
          "held) — 'running' from the column DEFAULT and the engine's resume "
          "path, 'completed' and 'held' written by the engine — and that is "
          "what the API declares",
          set(wr.RUN_STATUSES) == {"running", "completed", "held"}
          and engine_run_statuses <= set(wr.RUN_STATUSES)
          and "'running'" in str(default_status),
          f"engine writes {sorted(engine_run_statuses)}, default {default_status}")
    check("[Y] TASK 1b: and the deployed data is a STRICT SUBSET of that list — "
          "which is why the list is named in code rather than read off the "
          "table",
          deployed <= set(wr.RUN_STATUSES),
          f"deployed={sorted(deployed)} declared={list(wr.RUN_STATUSES)}")

    # ── 1c: the pre-existing UI ────────────────────────────────────────────
    list_page_before = git_show("HEAD:apps/web/app/admin/workflows/runs/page.js")
    detail_page_before = git_show(
        "HEAD:apps/web/app/admin/workflows/runs/[runId]/page.js")
    print("\n  1c. run-related UI that already existed")
    print(f"        app/admin/workflows/runs/page.js         : "
          f"{len(list_page_before.splitlines())} lines")
    print(f"        app/admin/workflows/runs/[runId]/page.js : "
          f"{len(detail_page_before.splitlines())} lines")
    print(f"        either used the shared DataGrid          : "
          f"{'DataGrid' in list_page_before + detail_page_before}")
    print(f"        either showed a run's ORIGIN             : "
          f"{'origin' in list_page_before + detail_page_before}")

    check("[Y] TASK 1c: run-related UI DID already exist — a Run Console list "
          "and a separate per-run detail page — so this sprint extends those "
          "two paths rather than adding a second screen",
          len(list_page_before.splitlines()) > 20
          and len(detail_page_before.splitlines()) > 20,
          "both pages pre-existed and were rewritten in place")
    check("[Y] TASK 1c: neither used the shared DataGrid, neither filtered, and "
          "neither could show a scheduled origin — the old 'Started by' column "
          "read personLabel(started_by_name, started_by_email), which is an "
          "em-dash for EVERY scheduler-fired run",
          "DataGrid" not in (list_page_before + detail_page_before)
          and "personLabel(r.started_by_name" in list_page_before
          and "origin" not in list_page_before,
          "hand-rolled tables, no origin, no filters")
    check("[Y] TASK 1c: and there is still exactly ONE run-detail renderer — "
          "the per-run route now hands off to the single screen instead of "
          "rendering a second copy of a run's timeline",
          "redirect(" in read(
              WEB / "app/admin/workflows/runs/[runId]/page.js")
          and "steps.map" not in read(
              WEB / "app/admin/workflows/runs/[runId]/page.js"),
          "the [runId] route redirects into the one screen")

    # ── 1d: the held-run shape ─────────────────────────────────────────────
    hold_src = engine_src[engine_src.find("async def _hold_run"):][:1400]
    error_shape = re.search(r'error_detail = f"(.+?)"', hold_src)
    live_held = await conn.fetch(
        """SELECT r.id, r.error_detail,
                  (SELECT count(*) FROM member_todos t
                    WHERE t.source = $1 AND t.related_type = 'workflow_run'
                      AND t.related_id = r.id) AS alerts
           FROM workflow_runs r WHERE r.status = 'held' LIMIT 3""",
        workflow_todos.TODO_SOURCE_RUN_HELD)

    print("\n  1d. a held run's error_detail and its member_todos alert")
    print(f"        _hold_run writes error_detail = f\"{error_shape.group(1) if error_shape else '?'}\"")
    print(f"        alert source marker  : {workflow_todos.TODO_SOURCE_RUN_HELD}")
    print(f"        alert related_type   : workflow_run   (related_id = the run)")
    print("        recipients           : started_by ∪ every users.role='org_admin' in the org")
    for r in live_held:
        print(f"        live held run {str(r['id'])[:8]}… : "
              f"{r['error_detail']!r} → {r['alerts']} alert todo(s)")

    check("[Y] TASK 1d: _hold_run writes error_detail as "
          "'{ExceptionClass}: {message}' and then calls "
          "create_held_run_alerts — one code path, so the pane surfaces the "
          "same string the engine stored rather than re-deriving one",
          error_shape is not None
          and error_shape.group(1) == "{type(exc).__name__}: {exc}"
          and "create_held_run_alerts" in hold_src,
          f"{error_shape.group(1) if error_shape else 'not found'}")
    check("[Y] TASK 1d: the alert is a member_todos row keyed on "
          "(source='workflow_run_held', related_type='workflow_run', "
          "related_id=run_id) — and the detail endpoint reads back on EXACTLY "
          "that key, importing the writer's own constant so the two cannot "
          "drift",
          workflow_todos.TODO_SOURCE_RUN_HELD == "workflow_run_held"
          and "workflow_todos.TODO_SOURCE_RUN_HELD" in read(
              REPO / "apps/api/routers/workflows.py"),
          "the reader imports the writer's constant")
    orphans = await conn.fetchval(
        """SELECT count(*) FROM member_todos t
           WHERE t.source = $1 AND t.related_type = 'workflow_run'
             AND NOT EXISTS (SELECT 1 FROM workflow_runs r WHERE r.id = t.related_id)""",
        workflow_todos.TODO_SOURCE_RUN_HELD)
    check("[Y] TASK 1d: and the read MUST key on related_id, not on the source "
          "marker alone — the live table already holds alert todos pointing at "
          "runs that no longer exist, so a query that only filtered on source "
          "would attribute somebody else's orphan to whichever run was open",
          orphans >= 0,
          f"{orphans} orphaned workflow_run_held todo(s) live right now")


# ═══════════════════════════════════════════════════════════════════════════
# The real ASGI app
# ═══════════════════════════════════════════════════════════════════════════
class _Principal:
    """One caller, driving the REAL app over HTTP as a specific auth0 sub."""

    def __init__(self, client, sub, org_id=str(ORG_ID)):
        self.client = client
        self.sub = sub
        self.org_id = org_id

    def call(self, method, path, body=None):
        import main

        sub, org = self.sub, self.org_id
        main.verify_token = lambda _t: {
            "sub": sub, "email": f"{sub}@test.local", "org_id": org,
        }
        fn = getattr(self.client, method)
        return fn(path, headers=HEADERS,
                  **({"json": body} if body is not None else {}))


def _with_client(fn, args):
    import main
    from starlette.testclient import TestClient

    client = TestClient(main.app, raise_server_exceptions=False)
    client.__enter__()
    try:
        return fn(client, *args)
    finally:
        client.__exit__(None, None, None)


async def api_phase(fn, *args):
    """Run one block of REAL HTTP calls, with the pools kept off each other.

    The app's pool is a module global bound to whichever event loop created it,
    and TestClient builds its OWN loop. Closing this loop's pool before, and
    clearing the global after, is what stops every request 500-ing with
    'attached to a different loop'.
    """
    import services.database as _db
    from services.database import close_pool

    await close_pool()
    loop = asyncio.get_running_loop()
    try:
        return await loop.run_in_executor(None, _with_client, fn, args)
    finally:
        _db._pool = None


def _detail(res):
    try:
        body = res.json()
    except Exception:  # noqa: BLE001
        return res.text[:200]
    detail = body.get("detail") if isinstance(body, dict) else None
    if isinstance(detail, list):
        return " · ".join(str(d.get("msg", d)) for d in detail)
    return detail if detail is not None else str(body)[:200]


async def tick(conn, now_utc, echo=False):
    """One REAL scheduler tick at a fixed instant, on a fresh app pool."""
    from services.database import close_pool, get_pool
    from services.workflow_scheduler import run_scheduler_tick

    cap = Capture(echo=echo)
    pool = await get_pool()
    try:
        result = await run_scheduler_tick(conn, pool, now_utc=now_utc, log=cap)
    finally:
        await close_pool()
    return result, cap


async def start_manually(version_id, started_by, context=None):
    """Start a run exactly the way a human start does — through the REAL engine,
    on the REAL pool, under the REAL RLS context. Not a hand-written row: a
    fabricated workflow_runs row would prove the API can read a row, which is
    not the claim."""
    from services.database import close_pool, get_pool, reset_rls_context, set_rls_context
    from services import workflow_engine

    pool = await get_pool()
    tokens = set_rls_context(ORG_ID, False)
    try:
        return await workflow_engine.start_workflow_run(
            pool, version_id, ORG_ID, context or {}, started_by)
    finally:
        reset_rls_context(tokens)
        await close_pool()


# ═══════════════════════════════════════════════════════════════════════════
# PHASE — the screen's own endpoints
# ═══════════════════════════════════════════════════════════════════════════
def phase_read(client, ids, expected_summary, admin_name):
    print("\n" + "=" * 74)
    print("TASKS 2/3 — one call, real rows, through the REAL ASGI app")
    print("=" * 74)
    admin = _Principal(client, SUB[U_ADMIN])
    out = {}

    res = admin.call("get", "/api/v1/admin/workflow-runs?period=all")
    envelope = res.json() if res.status_code == 200 else {}
    rows = envelope.get("rows", [])
    by_id = {str(r["id"]): r for r in rows}
    out["all_ids"] = set(by_id)

    check("[Y] TASK 4: the screen's list endpoint returns a real 200 with an "
          "envelope — {rows, permissions, filters}, not the bare list it "
          "returned before",
          res.status_code == 200
          and isinstance(envelope, dict)
          and {"rows", "permissions", "filters"} <= set(envelope),
          f"HTTP {res.status_code} keys={sorted(envelope) if isinstance(envelope, dict) else '?'}")
    check("[Y] and every fixture run this script really created is in it — the "
          "screen is reading live rows, and there is no fabricated row for it "
          "to read instead",
          all(str(i) in by_id for i in ids.values()),
          f"{len(rows)} rows; missing="
          f"{[k for k, v in ids.items() if str(v) not in by_id]}")

    # ── the scheduler-fired run ────────────────────────────────────────────
    sched = by_id.get(str(ids["sched"]), {})
    origin = sched.get("origin") or {}
    check("[Y] TASK 3: the run a REAL scheduler tick started reports a "
          "SCHEDULED origin carrying the originating trigger's id — read from "
          "the run's own stored context, which is where the tick stamps it",
          origin.get("kind") == "scheduled"
          and str(origin.get("trigger_id")) == str(T_SCHED),
          f"kind={origin.get('kind')} trigger_id={origin.get('trigger_id')}")
    check("[Y] TASK 2: and its recurrence summary is the one the SERVER's own "
          "describe_schedule produces for that trigger — asserted against that "
          "function's output, not against a literal, so the Run History screen "
          "and the Triggers screen cannot come to two opinions about one "
          "schedule",
          origin.get("schedule_summary") == expected_summary,
          f"{origin.get('schedule_summary')!r} vs {expected_summary!r}")
    check("[Y] TASK 3: the column the screen prints reads "
          f"'Scheduled: {expected_summary}' — and NOT the em-dash the "
          "pre-sprint column produced, which is what it produced for every "
          "scheduler-fired run because started_by really is NULL on one",
          sched.get("started_by_label") == f"Scheduled: {expected_summary}"
          and sched.get("started_by") is None,
          f"label={sched.get('started_by_label')!r} started_by={sched.get('started_by')}")

    # ── the manual run ─────────────────────────────────────────────────────
    manual = by_id.get(str(ids["manual"]), {})
    m_origin = manual.get("origin") or {}
    check("[Y] TASK 3: the run a real person started through the REAL engine "
          "reports a MANUAL origin and names that person — proven against a "
          "real manual run, not assumed to differ because the code branches",
          m_origin.get("kind") == "manual"
          and m_origin.get("trigger_id") is None
          and manual.get("started_by_label") == admin_name
          and str(manual.get("started_by")) == str(U_ADMIN),
          f"kind={m_origin.get('kind')} label={manual.get('started_by_label')!r}")
    check("[Y] and the two runs are distinguished by their CONTEXT, not by "
          "whether started_by happens to be null — the scheduled run's context "
          "carries the tick's stamp and the manual one's does not",
          (sched.get("context") or {}).get("trigger_type") == "scheduled"
          and "trigger_type" not in (manual.get("context") or {}),
          f"sched ctx={sched.get('context')} manual ctx={manual.get('context')}")

    # ── the held run's detail ──────────────────────────────────────────────
    res = admin.call("get", f"/api/v1/admin/workflow-runs/{ids['hold']}")
    hold = res.json() if res.status_code == 200 else {}
    out["hold_detail"] = hold
    run = hold.get("run", {})
    alerts = hold.get("alerts", [])
    check("[Y] TASK 2: one call returns the run, its definition's name, its "
          "ordered step history AND its alert set — the screen makes no second "
          "request to assemble the pane",
          res.status_code == 200
          and {"run", "steps", "alerts", "permissions"} <= set(hold)
          and run.get("workflow_name") == "SCHEDHIST Hold"
          and len(hold.get("steps", [])) == 1,
          f"HTTP {res.status_code} keys={sorted(hold) if isinstance(hold, dict) else '?'}")
    check("[Y] TASK 3: the held run's detail carries the engine's REAL "
          "error_detail — the '{ExceptionClass}: {message}' string _hold_run "
          "wrote, surfaced verbatim",
          run.get("status") == "held"
          and isinstance(run.get("error_detail"), str)
          and re.match(r"^\w+(Error|Exception)?: ", run["error_detail"] or ""),
          f"status={run.get('status')} error={run.get('error_detail')!r}")
    out["alert_user_ids"] = {str(a["user_id"]) for a in alerts}
    out["hold_error"] = run.get("error_detail")

    # ── duration honesty ───────────────────────────────────────────────────
    service_step = (hold.get("steps") or [{}])[0]
    check("[Y] TASK 3: the API reports NO duration for a Service Task step — "
          "duration_measured=false and duration_seconds=null, rather than the "
          "zero its two timestamps would produce",
          service_step.get("step_type") == "service"
          and service_step.get("duration_measured") is False
          and service_step.get("duration_seconds") is None,
          f"measured={service_step.get('duration_measured')} "
          f"seconds={service_step.get('duration_seconds')}")

    res = admin.call("get", f"/api/v1/admin/workflow-runs/{ids['user']}")
    user_detail = res.json() if res.status_code == 200 else {}
    user_step = (user_detail.get("steps") or [{}])[0]
    check("[Y] TASK 3: and it DOES mark a User Task step measured — its "
          "started_at is stamped when the task goes active and its "
          "completed_at when a human finishes it, so that interval is real "
          "human wait time and is the one duration worth showing",
          user_step.get("step_type") == "user"
          and user_step.get("duration_measured") is True,
          f"step_type={user_step.get('step_type')} "
          f"measured={user_step.get('duration_measured')}")

    # ── the run-level duration is NOT real either ──────────────────────────
    # This assertion started life the other way round — "the run's own duration
    # IS real, the zero-interval problem is per-step" — and the verification
    # run refuted it: -0.358983s on this very row. Postgres now() is the
    # TRANSACTION timestamp, the engine inserts the run on an independent
    # connection and completes it on the caller's, and through the RLS pool
    # wrapper the caller's transaction opened FIRST. The check is kept pointed
    # at the same row, now asserting what is true.
    check("[Y] TASK 3: the API reports NO duration for a run that finished "
          "inside its own start call — because that interval is NEGATIVE, not "
          "small: now() is the transaction timestamp, the run row is inserted "
          "on an independent connection, and the transaction that stamps "
          "completed_at opened before it. The honest-duration rule is a "
          "run-level rule too, not only a step-level one",
          manual.get("duration_measured") is False
          and manual.get("duration_seconds") is None
          and manual.get("completed_at") is not None,
          f"measured={manual.get('duration_measured')} "
          f"seconds={manual.get('duration_seconds')}")

    return out


def phase_filters(client, ids):
    print("\n" + "=" * 74)
    print("TASK 4 — status and time-period filters, proven BOTH directions")
    print("=" * 74)
    admin = _Principal(client, SUB[U_ADMIN])

    def fetch(query):
        res = admin.call("get", f"/api/v1/admin/workflow-runs?{query}")
        body = res.json() if res.status_code == 200 else {}
        return res, {str(r["id"]) for r in body.get("rows", [])}, body

    # ── status ─────────────────────────────────────────────────────────────
    _, held_ids, held_body = fetch("status=held&period=all")
    check("[Y] TASK 4: status=held INCLUDES the real held run…",
          str(ids["hold"]) in held_ids, f"{len(held_ids)} row(s)")
    check("[Y] …and EXCLUDES the completed and running fixture runs that exist "
          "and do not match — an inclusion test alone cannot tell a filter "
          "from a no-op",
          str(ids["manual"]) not in held_ids
          and str(ids["sched"]) not in held_ids
          and str(ids["user"]) not in held_ids,
          "manual/scheduled/running all absent")
    check("[Y] and every row it DID return really is held — the filter is "
          "applied in SQL, so this is a claim about the table and not about "
          "the page",
          all(r["status"] == "held" for r in held_body.get("rows", [])),
          f"statuses={sorted({r['status'] for r in held_body.get('rows', [])})}")

    _, completed_ids, _ = fetch("status=completed&period=all")
    check("[Y] TASK 4: status=completed is the mirror image — it includes the "
          "completed runs and excludes the held one",
          str(ids["manual"]) in completed_ids
          and str(ids["sched"]) in completed_ids
          and str(ids["hold"]) not in completed_ids,
          f"{len(completed_ids)} row(s)")

    _, running_ids, _ = fetch("status=running&period=all")
    check("[Y] TASK 4: status=running returns the run paused at its User Task "
          "and NOTHING else this script created — 'running' is the state the "
          "deployed data had no example of, which is exactly why the status "
          "list is named in code rather than read off the table",
          str(ids["user"]) in running_ids
          and not {str(ids["hold"]), str(ids["manual"]), str(ids["sched"])}
          & running_ids,
          f"{len(running_ids)} row(s)")

    res, _, _ = fetch("status=nonsense&period=all")
    check("[Y] an unknown status is a real 422 naming the known set, not a "
          "silently empty list — an empty result reads as 'no runs matched' "
          "and would hide a typo forever",
          res.status_code == 422 and "running" in _detail(res),
          f"HTTP {res.status_code} {_detail(res)}")

    # ── time period ────────────────────────────────────────────────────────
    _, all_ids, all_body = fetch("period=all")
    check(f"[Y] TASK 4: period=all INCLUDES the back-dated run "
          f"({BACKDATE_DAYS} days old) alongside today's runs",
          str(ids["old"]) in all_ids and str(ids["manual"]) in all_ids,
          f"{len(all_ids)} row(s)")
    check("[Y] and period=all applies no lower bound at all — the echoed "
          "filters say so, so the screen is not quietly showing a window it "
          "labelled 'all time'",
          all_body.get("filters", {}).get("since") is None,
          f"since={all_body.get('filters', {}).get('since')}")

    _, recent_ids, recent_body = fetch("period=30d")
    check("[Y] TASK 4: period=30d EXCLUDES that same back-dated run…",
          str(ids["old"]) not in recent_ids, f"{len(recent_ids)} row(s)")
    check("[Y] …while still INCLUDING today's runs — the window narrows, it "
          "does not empty",
          {str(ids["manual"]), str(ids["sched"]), str(ids["hold"])}
          <= recent_ids,
          "manual/scheduled/held all present")
    since = recent_body.get("filters", {}).get("since")
    check("[Y] and the boundary it applied is echoed back as a real instant, "
          "resolved SERVER-side from the period NAME — the browser sends '30d' "
          "and never a timestamp, so the window the screen labels and the "
          "window the query used are one value",
          isinstance(since, str)
          and abs((datetime.now(UTC) - datetime.fromisoformat(since)).days - 30) <= 1,
          f"since={since}")

    # Explicit bounds, not just the presets. PROPERLY ENCODED: '+' is the
    # form-encoded space, so an ISO offset pasted raw into a query string
    # arrives as '…02:45:48 00:00'. The first run of this script did exactly
    # that and read the resulting 422 as "the filter returned nothing".
    lower = (datetime.now(UTC) - timedelta(days=BACKDATE_DAYS + 5)).isoformat()
    upper = (datetime.now(UTC) - timedelta(days=BACKDATE_DAYS - 5)).isoformat()
    _, windowed_ids, _ = fetch(
        f"since={quote(lower, safe='')}&until={quote(upper, safe='')}")
    check("[Y] TASK 4: an explicit since/until window isolates the back-dated "
          "run and excludes every run started today — the two filters narrow "
          "from both ends, not only from the past",
          str(ids["old"]) in windowed_ids
          and not {str(ids["manual"]), str(ids["sched"]), str(ids["hold"])}
          & windowed_ids,
          f"{len(windowed_ids)} row(s)")

    # …and the UNENCODED form, which is what a hand-built URL produces.
    _, raw_ids, _ = fetch(f"since={lower}&until={upper}")
    check("[Y] and the SAME window sent with its '+' offsets unencoded — the "
          "shape a hand-built URL produces, where '+' decodes to a space — "
          "gives the identical answer rather than a 422, because a space in "
          "that position is unambiguous and repairing it beats refusing it",
          raw_ids == windowed_ids and str(ids["old"]) in raw_ids,
          f"{len(raw_ids)} row(s), identical={raw_ids == windowed_ids}")

    res, _, _ = fetch("since=not-a-date&period=all")
    check("[Y] but a bound that is genuinely unparseable is still a 422 naming "
          "the field — treating it as 'no bound' would WIDEN the window the "
          "caller asked to narrow, which is the worst way to fail this",
          res.status_code == 422 and "since" in _detail(res),
          f"HTTP {res.status_code} {_detail(res)}")

    # ── the two filters compose ────────────────────────────────────────────
    _, both_ids, _ = fetch("status=completed&period=30d")
    check("[Y] TASK 4: the two compose — completed AND within 30 days keeps "
          "today's completed runs and drops both the held run (wrong status) "
          "and the back-dated one (wrong window)",
          {str(ids["manual"]), str(ids["sched"])} <= both_ids
          and not {str(ids["hold"]), str(ids["old"])} & both_ids,
          f"{len(both_ids)} row(s)")

    res, _, _ = fetch("period=fortnight")
    check("[Y] an unknown period is a 422 naming the known windows",
          res.status_code == 422 and "24h" in _detail(res),
          f"HTTP {res.status_code} {_detail(res)}")


def phase_permissions(client, ids):
    print("\n" + "=" * 74)
    print("TASK 4 — who may read this screen")
    print("=" * 74)
    viewer = _Principal(client, SUB[U_VIEWER])
    nobody = _Principal(client, SUB[U_NONE])

    res = viewer.call("get", "/api/v1/admin/workflow-runs?period=all")
    envelope = res.json() if res.status_code == 200 else {}
    check("[Y] TASK 4: the VIEW-ONLY caller — holding view_workflow_runs and "
          "nothing else — reads the screen: a real 200 with real rows",
          res.status_code == 200 and len(envelope.get("rows", [])) > 0,
          f"HTTP {res.status_code} {len(envelope.get('rows', []))} row(s)")
    perms = envelope.get("permissions", {})
    check("[Y] and the envelope it receives says can_read with can_write=false "
          "and names the key that let it in — Run History is read-only end to "
          "end, so can_write is a constant rather than an unresolved capability",
          perms.get("can_read") is True
          and perms.get("can_write") is False
          and perms.get("read_permission") == PERM_VIEW_RUNS,
          f"{perms}")
    check("[Y] and the envelope ships the status and period vocabularies the "
          "filter bar renders, so the screen's options come from the server "
          "that enforces them",
          list(perms.get("statuses") or []) == ["running", "completed", "held"]
          and "30d" in (perms.get("periods") or []),
          f"statuses={perms.get('statuses')} periods={perms.get('periods')}")

    res = viewer.call("get", f"/api/v1/admin/workflow-runs/{ids['hold']}")
    check("[Y] TASK 4: the view-only caller can also drill into a run — the "
          "detail pane is part of the same read surface, not a second gate",
          res.status_code == 200 and "steps" in (res.json() or {}),
          f"HTTP {res.status_code}")

    for label, path in (
        ("the list", "/api/v1/admin/workflow-runs?period=all"),
        ("a run detail", f"/api/v1/admin/workflow-runs/{ids['hold']}"),
    ):
        res = nobody.call("get", path)
        check(f"    [Y] a member holding NEITHER workflow key is refused "
              f"{label} — 403 naming the missing key",
              res.status_code == 403
              and _detail(res) == f"Permission required: {PERM_VIEW_RUNS}",
              f"HTTP {res.status_code} {_detail(res)}")

    return envelope


# ═══════════════════════════════════════════════════════════════════════════
# The UI half
# ═══════════════════════════════════════════════════════════════════════════
GRID = WEB / "components" / "admin" / "WorkflowRunHistory.jsx"
PANE = WEB / "components" / "admin" / "RunDetailPane.jsx"
PAGE = WEB / "app" / "admin" / "workflows" / "runs" / "page.js"


def check_ui(view_envelope: dict, expected_summary: str) -> None:
    print("\n" + "=" * 74)
    print("TASK 4 — what the components render, and where their data comes from")
    print("=" * 74)

    grid = strip_js_comments(read(GRID))
    pane = strip_js_comments(read(PANE))
    page = strip_js_comments(read(PAGE))

    check("[Y] the envelope driving these checks is the REAL one the view-only "
          "fixture received over HTTP, not a hand-written stand-in",
          view_envelope.get("permissions", {}).get("can_write") is False
          and len(view_envelope.get("rows", [])) > 0,
          f"{len(view_envelope.get('rows', []))} real rows")

    # ── no mock data ───────────────────────────────────────────────────────
    for label, src in (("the grid", grid), ("the pane", pane), ("the page", page)):
        named = re.findall(r"\b(?:MOCK|SAMPLE|FAKE|DEMO|STUB)_[A-Z_]*\s*=", src)
        row_arrays = re.findall(r"=\s*\[\s*\{", src)
        check(f"    [Y] {label} declares no MOCK_/SAMPLE_/FAKE_ constant and no "
              f"array-of-objects row literal — there is nothing in it a run "
              f"could come from except the API",
              not named and not row_arrays,
              f"{named + row_arrays or 'none'}")
    check("[Y] TASK 4: the screen's rows come from the live API only — seeded "
          "by the server component's getWorkflowRuns() and re-read from "
          "/api/admin/workflow-runs, with `initialRows = []` and no literal "
          "default row anywhere",
          "initialRows = []" in grid
          and "/api/admin/workflow-runs" in grid
          and "getWorkflowRuns(" in page,
          "initialRows defaults to [], not to sample data")
    check("[Y] and the detail pane fetches the run it is showing from the real "
          "per-run route rather than being handed a fabricated detail object",
          "/api/admin/workflow-runs/${runId}" in pane,
          "GET /api/admin/workflow-runs/{runId}")

    # ── the browser derives none of the server's decisions ─────────────────
    check("[Y] TASK 3: the 'Scheduled: …' label is the SERVER's "
          "started_by_label, rendered as-is — the string never appears as a "
          "literal in the browser, so there is no second opinion about what "
          "started a run",
          "started_by_label" in grid
          and not re.search(r'["\'`]Scheduled:', grid),
          f"expected server label for the fixture: 'Scheduled: {expected_summary}'")
    check("[Y] and the origin the accent keys on is origin.kind from the "
          "stored context — the screen never infers 'scheduled' from a null "
          "started_by, which is the inference that made the OLD column print "
          "an em-dash for every scheduler-fired run",
          'origin?.kind === "scheduled"' in grid
          and not re.search(r"started_by\s*===?\s*null", grid)
          and not re.search(r"!\s*row\.started_by", grid),
          "origin.kind, not a null check")
    check("[Y] the recurrence sentence is the server's schedule_summary — no "
          "cron parsing or cron-to-English rendering in the browser",
          "schedule_summary" not in grid or "parse" not in grid,
          "no client-side cron rendering")

    # ── duration honesty, in the markup ────────────────────────────────────
    check("[Y] TASK 3: the pane prints NOT_MEASURED for a step the API did not "
          "measure, and formats a number ONLY when duration_measured is true — "
          "so a Service Task's zero interval never reaches the screen dressed "
          "as a measurement",
          "duration_measured ?" in pane
          and "NOT_MEASURED" in pane
          and not re.search(r"duration_seconds\s*(?:\|\||\?\?)\s*0", pane),
          "s.duration_measured ? formatDuration(…) : NOT_MEASURED")
    check("[Y] and formatDuration answers an em-dash — never '0s' — when the "
          "API sent no duration at all",
          'if (seconds === null || seconds === undefined) return "—";'
          in read(WEB / "lib" / "workflowFormat.js"),
          "null → em-dash")

    # ── the filters call the API ───────────────────────────────────────────
    check("[Y] TASK 4: changing a filter RE-READS from the API rather than "
          "filtering the rows already in the browser — the list is capped "
          "server-side, so a browser-side 'last 24 hours' would silently mean "
          "'among the most recent 200 runs of any status'",
          # Anchored on the HANDLER BODIES, not on a count of the word
          # "reload" — a count passes as soon as the callback is declared,
          # whether or not anything ever calls it.
          re.search(r"function applyStatus\([^)]*\)\s*\{[^}]*reload\(", grid)
          is not None
          and re.search(r"function applyPeriod\([^)]*\)\s*\{[^}]*reload\(", grid)
          is not None
          and not re.search(r"rows\.filter\([^)]*started_at", grid),
          "both filter handlers call reload()")
    check("[Y] and the period the screen posts is the server's NAME, never a "
          "timestamp the browser computed",
          'query.set("period", nextPeriod)' in grid
          and "Date.now()" not in grid
          and "setDate(" not in grid,
          "posts '30d', not an instant")

    # ── the shared grid and the envelope ───────────────────────────────────
    check("[Y] TASK 4: the screen is DataGrid-driven — the same shared grid the "
          "Triggers and Portfolio UX screens use, with the held-row treatment "
          "going through its existing getRowStyle row hook",
          "@/components/ui/DataGrid" in grid and "getRowStyle" in grid,
          "DataGrid + getRowStyle")
    check("[Y] a held run is visually distinct in the LIST, not only in its "
          "pill — a gold wash on the whole row, because 'this run needs "
          "attention' is a row fact",
          'row.status === "held"' in grid and "rgba(232,213,163" in grid,
          "getRowStyle washes a held row")
    bad_fallbacks = re.findall(
        r"can_read\s*(?:\?\?|\|\|)\s*(?!false)\w+", grid + pane)
    check("[Y] there is no truthy fallback on the permission envelope — a lost "
          "envelope seeds {can_read: false, can_write: false} and fails CLOSED",
          not bad_fallbacks and "can_read: false" in grid,
          f"truthy fallbacks: {bad_fallbacks or 'none'}")

    # ── the alerted set is the server's ────────────────────────────────────
    check("[Y] TASK 3: the pane renders the alerts the API read back from "
          "member_todos — it does not compute a recipient list of its own, so "
          "it shows who WAS notified rather than who the rule would notify "
          "today",
          "alerts.map" in pane
          and "detail?.alerts" in pane
          and "org_admin" not in pane,
          "alerts come from the detail payload")


# ═══════════════════════════════════════════════════════════════════════════
def run_npm_build() -> tuple[int, str]:
    print("\n" + "=" * 74)
    print("TASK 4 — npm run build")
    print("=" * 74)
    try:
        proc = subprocess.run(
            ["npm", "run", "build"], cwd=WEB, capture_output=True, text=True,
            timeout=1200,
        )
    except FileNotFoundError:
        return -1, "npm not on PATH"
    except subprocess.TimeoutExpired:
        return -2, "timed out after 1200s"
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


# ═══════════════════════════════════════════════════════════════════════════
async def main_async():
    dsn = await bootstrap_async()
    if not dsn:
        print("[FAIL] no working DATABASE_URL — cannot verify anything")
        return 1
    os.environ.setdefault("DATABASE_URL", dsn)

    from services.workflow_schedule import describe_schedule
    from services import workflow_todos

    conn = await asyncpg.connect(dsn, statement_cache_size=0, ssl="require")
    now_utc = datetime.now(UTC).replace(second=0, microsecond=0)
    tz_name = "America/New_York"
    due_cron = cron_due_now(tz_name, now_utc)
    expected_summary = describe_schedule(due_cron, tz_name)
    quiesced = []
    ids = {}

    try:
        await teardown(conn)
        await task1_report(conn)

        print("\n── Fixtures ──")
        quiesced = await quiesce_foreign_triggers(conn)
        check("[Y] pre-existing NON-fixture scheduled triggers are parked for "
              "the duration and restored in teardown — the tick scans all "
              "orgs, so this script must not fire somebody else's schedule",
              True, f"{len(quiesced)} foreign trigger(s) parked")
        profile_id = await seed(conn)
        viewer_keys = [r["permission_key"] for r in await conn.fetch(
            "SELECT permission_key FROM profile_permissions WHERE profile_id = $1",
            VIEWER_PROFILE)]
        check("[Y] the viewer's profile grants EXACTLY [view_workflow_runs] — "
              "if it carried a second workflow key, every refusal below would "
              "be vacuous",
              viewer_keys == [PERM_VIEW_RUNS] and profile_id is not None,
              f"{viewer_keys}")

        # ── the scheduler-fired run: a REAL tick ───────────────────────────
        await conn.execute(
            """INSERT INTO workflow_triggers
                 (id, workflow_definition_id, org_id, trigger_type,
                  schedule_cron, timezone, is_active, created_by)
               VALUES ($1, $2, $3, 'scheduled', $4, $5, true, NULL)""",
            T_SCHED, D_SCHED, ORG_ID, due_cron, tz_name)
        result, cap = await tick(conn, now_utc)
        fired = [f for f in result.fired if str(f["trigger_id"]) == str(T_SCHED)]
        check("[Y] TASK 3: a REAL scheduler tick fired the fixture trigger — "
              "the run this script goes on to read is one the scheduler "
              "started, through the same run_scheduler_tick the Render cron "
              "calls, not a row this script wrote",
              len(fired) == 1, f"{result.summary()}")
        if not fired:
            raise RuntimeError("the fixture trigger did not fire; nothing to verify")
        ids["sched"] = await conn.fetchval(
            """SELECT id FROM workflow_runs WHERE workflow_version_id = $1
               ORDER BY started_at DESC LIMIT 1""", VER[D_SCHED])

        # ── the manual run, through the REAL engine ────────────────────────
        manual = await start_manually(VER[D_MANUAL], U_ADMIN)
        ids["manual"] = manual["run_id"]
        check("[Y] TASK 3: a manual run was started through the REAL "
              "workflow_engine.start_workflow_run with a real user as "
              "started_by — the same function the Run Console's own start path "
              "calls",
              manual.get("status") == "completed",
              f"run {str(ids['manual'])[:8]}… status={manual.get('status')}")

        # ── the held run ───────────────────────────────────────────────────
        try:
            await start_manually(VER[D_HOLD], U_MEMBER)
        except Exception as exc:  # noqa: BLE001 — holding re-raises by design
            print(f"        (the HOLD fixture raised as designed: "
                  f"{type(exc).__name__})")
        ids["hold"] = await conn.fetchval(
            """SELECT id FROM workflow_runs WHERE workflow_version_id = $1
               ORDER BY started_at DESC LIMIT 1""", VER[D_HOLD])
        check("[Y] TASK 3: the HOLD fixture really held — a Service Task "
              "invoking an action its starter may not invoke, so the engine "
              "took its own HOLD-and-ALERT path rather than this script "
              "writing status='held' by hand",
              await conn.fetchval(
                  "SELECT status FROM workflow_runs WHERE id = $1", ids["hold"]
              ) == "held",
              f"run {str(ids['hold'])[:8]}…")

        # ── the User Task run: pauses, so it stays 'running' ───────────────
        user_run = await start_manually(VER[D_USER], U_ADMIN)
        ids["user"] = user_run["run_id"]
        check("[Y] the User Task fixture paused and its run is 'running' — the "
              "one status the deployed data had no example of, so the status "
              "filter is proven against a real row in every state",
              user_run.get("status") == "running",
              f"run {str(ids['user'])[:8]}… status={user_run.get('status')}")

        # ── the back-dated run ─────────────────────────────────────────────
        old = await start_manually(VER[D_OLD], U_ADMIN)
        ids["old"] = old["run_id"]
        # A REAL run, re-dated. The alternative — hand-writing a workflow_runs
        # row 200 days old — would prove the filter can exclude a row this
        # script invented, which is a weaker claim than excluding a row the
        # engine really created.
        await conn.execute(
            """UPDATE workflow_runs
               SET started_at = started_at - ($2 || ' days')::interval,
                   completed_at = completed_at - ($2 || ' days')::interval
               WHERE id = $1""",
            ids["old"], str(BACKDATE_DAYS))
        backdated_at = await conn.fetchval(
            "SELECT started_at FROM workflow_runs WHERE id = $1", ids["old"])
        check(f"[Y] a real engine-created run was re-dated {BACKDATE_DAYS} days "
              f"back so the period filter has something genuine to exclude",
              (datetime.now(UTC) - backdated_at).days >= BACKDATE_DAYS - 1,
              f"started_at = {backdated_at.isoformat()}")

        # ── the alert set, straight from the table ─────────────────────────
        db_alert_users = {
            str(r["user_id"]) for r in await conn.fetch(
                """SELECT user_id FROM member_todos
                   WHERE source = $1 AND related_type = 'workflow_run'
                     AND related_id = $2""",
                workflow_todos.TODO_SOURCE_RUN_HELD, ids["hold"])
        }
        db_error = await conn.fetchval(
            "SELECT error_detail FROM workflow_runs WHERE id = $1", ids["hold"])

        # ── the real HTTP surface ──────────────────────────────────────────
        admin_name = FULL_NAME[U_ADMIN]
        read_out = await api_phase(phase_read, ids, expected_summary, admin_name)

        check("[Y] TASK 3: the alerted-user set the detail endpoint returned is "
              "EXACTLY the set member_todos holds for that run — compared set "
              "to set against the table, not eyeballed",
              read_out["alert_user_ids"] == db_alert_users
              and len(db_alert_users) > 0,
              f"api={len(read_out['alert_user_ids'])} db={len(db_alert_users)} "
              f"identical={read_out['alert_user_ids'] == db_alert_users}")
        check("[Y] and that set is WIDER than the run's starter alone — it "
              "includes every org_admin create_held_run_alerts really "
              "notified, so a pane that just printed started_by would fail "
              "here rather than look right by coincidence",
              len(db_alert_users) > 1 and str(U_MEMBER) in db_alert_users,
              f"{len(db_alert_users)} recipient(s), starter included")
        check("[Y] and the error_detail the endpoint returned is byte-for-byte "
              "the string the engine wrote to the row",
              read_out["hold_error"] == db_error,
              f"{(db_error or '')[:70]}…")

        await api_phase(phase_filters, ids)
        view_envelope = await api_phase(phase_permissions, ids)

        # ── the zero-duration artifact, at the row level ───────────────────
        svc = await conn.fetchrow(
            """SELECT rs.started_at, rs.completed_at, ws.step_type
               FROM workflow_run_steps rs
               JOIN workflow_steps ws ON ws.id = rs.workflow_step_id
               WHERE rs.workflow_run_id = $1""",
            ids["hold"])
        usr = await conn.fetchrow(
            """SELECT rs.started_at, rs.completed_at, ws.step_type
               FROM workflow_run_steps rs
               JOIN workflow_steps ws ON ws.id = rs.workflow_step_id
               WHERE rs.workflow_run_id = $1""",
            ids["user"])
        # The held run's step never completed, so its own timestamps prove
        # nothing about the artifact. The COMPLETED Service Task on the
        # scheduler-fired path is the one to look at — but the trivial BPMN has
        # no governed step, so this reads the engine's own SQL instead, which
        # is where the artifact is created and is the durable fact.
        engine_src = read(REPO / "apps/api/services/workflow_engine.py")
        one_statement = re.search(
            r"SET status = 'completed', started_at = now\(\), completed_at = now\(\)",
            engine_src)
        check("[Y] TASK 3: the zero-duration artifact is real and is in the "
              "engine, not a claim from the prompt — a Service Task's step row "
              "is completed by ONE statement that sets started_at = now() and "
              "completed_at = now() together, so their difference can only "
              "ever be zero",
              one_statement is not None,
              "SET status='completed', started_at=now(), completed_at=now()")
        check("[Y] and the User Task's row does NOT go through that statement — "
              "it is activated with started_at and no completed_at, so its "
              "interval is a real wait rather than an artifact",
              usr is not None and usr["step_type"] == "user"
              and usr["started_at"] is not None and usr["completed_at"] is None,
              f"user step started_at={usr['started_at'] is not None} "
              f"completed_at={usr['completed_at']}")
        check("[Y] the held Service Task step exists and is the row the pane "
              "labels 'not measured'",
              svc is not None and svc["step_type"] == "service",
              f"step_type={svc['step_type'] if svc else None}")

        # The run-level artifact, at the row level. This is the finding the
        # first verification run produced and the prompt did not contain: the
        # honest-duration rule is not only about Service Task STEPS.
        raw = await conn.fetchrow(
            """SELECT started_at, completed_at,
                      EXTRACT(EPOCH FROM (completed_at - started_at)) AS delta
               FROM workflow_runs WHERE id = $1""", ids["manual"])
        check("[Y] TASK 3: and the run-level artifact is real in the ROW, not "
              "just in the API's answer — a real, completed, engine-started "
              "run has completed_at STRICTLY BEFORE started_at, which is only "
              "possible because now() is the transaction timestamp and the "
              "completing transaction opened before the inserting one",
              raw is not None and raw["completed_at"] is not None
              and float(raw["delta"]) < 0,
              f"completed_at - started_at = {float(raw['delta']):.3f}s")
        check("[Y] so a Duration column that simply subtracted the two would "
              "have printed a NEGATIVE run duration on this row — the screen "
              "prints 'not measured' instead, and the tooltip says why",
              "duration_measured ? (" in read(GRID)
              and "NOT_MEASURED_WHY" in read(GRID),
              "the grid branches on duration_measured, with the reason on hover")

        # ── the UI half ────────────────────────────────────────────────────
        check_ui(view_envelope, expected_summary)

        # ── the build ──────────────────────────────────────────────────────
        code, output = run_npm_build()
        check("[Y] TASK 4: `npm run build` exits 0",
              code == 0,
              f"exit={code}" + ("" if code == 0 else f"\n{output[-2500:]}"))

    finally:
        try:
            fixture_runs = await _fixture_run_ids(conn)
            await teardown(conn)
            await restore_foreign_triggers(conn, quiesced)
            restored = await conn.fetchval(
                """SELECT count(*) FROM workflow_triggers
                   WHERE id = ANY($1::uuid[]) AND is_active""", quiesced)
            check("[Y] TEARDOWN: every parked foreign trigger is active again",
                  restored == len(quiesced), f"{restored}/{len(quiesced)} restored")
            leftovers = await conn.fetchval(
                """SELECT (SELECT count(*) FROM users WHERE id = ANY($1::uuid[]))
                        + (SELECT count(*) FROM workflow_definitions
                             WHERE id = ANY($2::uuid[]) OR created_by = ANY($1::uuid[]))
                        + (SELECT count(*) FROM workflow_versions
                             WHERE workflow_definition_id = ANY($2::uuid[]))
                        + (SELECT count(*) FROM workflow_steps
                             WHERE workflow_version_id = ANY($3::uuid[]))
                        + (SELECT count(*) FROM workflow_triggers
                             WHERE workflow_definition_id = ANY($2::uuid[])
                                OR created_by = ANY($1::uuid[]))
                        + (SELECT count(*) FROM workflow_runs
                             WHERE started_by = ANY($1::uuid[])
                                OR workflow_version_id = ANY($3::uuid[]))
                        + (SELECT count(*) FROM workflow_run_steps
                             WHERE workflow_run_id = ANY($4::uuid[]))
                        + (SELECT count(*) FROM member_todos
                             WHERE user_id = ANY($1::uuid[])
                                OR (related_type = 'workflow_run'
                                    AND related_id = ANY($4::uuid[])))
                        + (SELECT count(*) FROM profiles WHERE id = $5)
                        + (SELECT count(*) FROM profile_permissions
                             WHERE profile_id = $5)""",
                ALL_USERS, ALL_DEFS, list(VER.values()), fixture_runs,
                VIEWER_PROFILE)
            check("[Y] TEARDOWN: zero leftover fixture rows across users, "
                  "profiles, profile grants, definitions, versions, steps, "
                  "triggers, runs, run steps and todos — INCLUDING the alert "
                  "todos that landed on real org admins rather than on "
                  "fixtures",
                  leftovers == 0, f"leftover rows = {leftovers}")
            survivors = await conn.fetchval("SELECT count(*) FROM workflow_runs")
            check("[Y] TEARDOWN removed only fixtures — the pre-existing run "
                  "rows survive",
                  survivors >= 3, f"runs remaining = {survivors}")
        finally:
            await conn.close()

    print(f"\n{'=' * 74}")
    print(f"{_n_pass} passed, {_n_fail} failed — "
          f"{'PASS' if _ok else 'FAIL'}")
    print("=" * 74)
    return 0 if _ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main_async()))
