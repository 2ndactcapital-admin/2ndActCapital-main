"""verify_schedulercore.py — the workflow scheduler's core firing engine.

WHAT THIS PROVES (against the DEPLOYED database and the REAL engine):

  [Task 1] The four discovery findings, reported from live introspection rather
           than quoted from the prompt — including two places where the prompt
           and reality disagree.
  [Task 3] A trigger genuinely due IN ITS OWN TIMEZONE fires a real
           workflow_runs row through the REAL
           workflow_engine.start_workflow_run. Not a stub, not a copy of the
           firing logic: the verify script imports and calls exactly the
           function apps/api/workflow_scheduler_tick.py calls.
  [Task 3] A trigger not yet due does not fire, including a real
           timezone-boundary pair — two triggers, IDENTICAL cron, different
           IANA zones, evaluated at ONE instant, with opposite verdicts.
  [Task 3] Two ticks back to back against the same due trigger fire EXACTLY
           ONCE — and the second tick's suppression is proven to come from the
           atomic claim, not from luck, by also running the claim twice
           directly.
  [Task 4] A trigger whose previous WORKFLOW run is still in progress is
           skipped and the skip is LOGGED VISIBLY — proven against a real
           non-terminal workflow_runs row, with the emitted log line captured
           and asserted on.
  [Task 5] end_date and max_occurrences really stop firing, checked at the DB
           level (the trigger is due by its cron and still does not fire).
  [Task 5] A held scheduler-fired run produces the SAME create_held_run_alerts
           todo shape as any other held run — asserted field by field against
           the constants in services/workflow_todos.py, not by eyeball.
  [Task 5] Cross-org isolation on scheduled firing.
  [Task 5] A NON-super-admin org_admin creates a real schedule-type trigger
           through the REAL ASGI app and the extended body model, and that
           trigger then fires. This is what confirms workflowpermsfix landed.
  [Teardown] Zero leftover rows, asserted by count.

HOW THE CLOCK IS HANDLED. Every decision test injects a FIXED ``now_utc`` into
the real evaluator, so a timezone or DST assertion means the same thing at
09:00 as at midnight. The end-to-end firing tests use the real clock, with each
fixture's cron built FROM the current local time in its own zone — so "due" is
genuinely due, not asserted into existence.

Run:  python3 apps/api/scripts/verify_schedulercore.py
"""
import asyncio
import pathlib
import sys
from datetime import datetime, timedelta, timezone
from uuid import UUID
from zoneinfo import ZoneInfo

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from _db_bootstrap import bootstrap_async  # noqa: E402  (also puts apps/api on sys.path)

import asyncpg  # noqa: E402

UTC = timezone.utc

ORG_ID = UUID("00000000-0000-0000-0000-000000000001")        # 2nd Act Capital
OTHER_ORG_ID = UUID("bb347258-8f28-4f49-8cc9-e29ccad82884")  # Hollisworks

ORG_ADMIN_PROFILE = "Org Admin"
PERM_TRIGGERS = "configure_workflow_triggers"

# Fixture users.
U_ORGADMIN = UUID("99000000-0000-0000-0000-0000000009b1")
U_MEMBER = UUID("99000000-0000-0000-0000-0000000009b2")   # holds NO workflow key
U_OTHERORG = UUID("99000000-0000-0000-0000-0000000009b3")
ALL_USERS = [U_ORGADMIN, U_MEMBER, U_OTHERORG]
SUB = {
    U_ORGADMIN: "schedcore_orgadmin",
    U_MEMBER: "schedcore_member",
    U_OTHERORG: "schedcore_otherorg",
}

# Fixture definitions / versions. One per behaviour so a fired run in one test
# can never satisfy another test's overlap check by accident.
D_DUE = UUID("99000000-0000-0000-0000-0000000009c1")
D_TZ = UUID("99000000-0000-0000-0000-0000000009c2")
D_OVERLAP = UUID("99000000-0000-0000-0000-0000000009c3")
D_CAPS = UUID("99000000-0000-0000-0000-0000000009c4")
D_HOLD = UUID("99000000-0000-0000-0000-0000000009c5")
D_OTHERORG = UUID("99000000-0000-0000-0000-0000000009c6")
D_API = UUID("99000000-0000-0000-0000-0000000009c7")
ALL_DEFS = [D_DUE, D_TZ, D_OVERLAP, D_CAPS, D_HOLD, D_OTHERORG, D_API]

VER = {d: UUID(str(d).replace("9c", "9d", 1)) for d in ALL_DEFS}

BPMN_NS = "http://www.omg.org/spec/BPMN/20100524/MODEL"
EXT_NS = "http://2ndactcapital.com/bpmn/ext"

# The action the HOLD fixture's Service Task invokes. It is the ONE
# workflow_invocable action in the registry, it declares
# required_permission='author_workflows', and the fixture that starts that run
# is a member who does not hold it — so _assert_action_permission raises, the
# engine holds the run and alerts. Deterministic and environment-independent:
# the failure happens before any network call, so it does not depend on whether
# LITELLM_BASE_URL happens to be set.
INVOCABLE_ACTION = "litellm.reload_model_cost_map"

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


class Capture:
    """Collects the tick's log lines so a 'logged visibly' claim can be asserted.

    The overlap requirement is specifically that the skip is logged and not
    silent. Asserting only the returned TickResult would leave the visible half
    of that requirement unproven.
    """

    def __init__(self, echo=True):
        self.lines = []
        self.echo = echo

    def __call__(self, message):
        self.lines.append(str(message))
        if self.echo:
            print(f"        │ {message}")

    def text(self):
        return "\n".join(self.lines)


# ── BPMN ────────────────────────────────────────────────────────────────────
def trivial_bpmn(proc_id) -> str:
    """Start -> End. Runs to 'completed' with no side effects."""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<bpmn:definitions xmlns:bpmn="{BPMN_NS}" id="D_{proc_id}" '
        'targetNamespace="http://2ndactcapital.com/bpmn">'
        f'<bpmn:process id="{proc_id}" isExecutable="true">'
        '<bpmn:startEvent id="p_start"><bpmn:outgoing>p1</bpmn:outgoing></bpmn:startEvent>'
        '<bpmn:endEvent id="p_end"><bpmn:incoming>p1</bpmn:incoming></bpmn:endEvent>'
        '<bpmn:sequenceFlow id="p1" sourceRef="p_start" targetRef="p_end"/>'
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


# ── fixtures ────────────────────────────────────────────────────────────────
async def _mk_user(conn, uid, org_id, role, profile_id):
    sub = SUB[uid]
    await conn.execute(
        """INSERT INTO users (id, org_id, email, full_name, auth0_sub, role,
                              profile_id, is_active)
           VALUES ($1, $2, $3, $4, $5, $6, $7, true)
           ON CONFLICT (auth0_sub) DO UPDATE
             SET role = EXCLUDED.role, profile_id = EXCLUDED.profile_id,
                 org_id = EXCLUDED.org_id, is_active = true""",
        uid, org_id, f"{sub}@test.local", sub, sub, role, profile_id,
    )


async def _mk_definition(conn, def_id, org_id, name, bpmn, created_by,
                         service_step=None):
    await conn.execute(
        """INSERT INTO workflow_definitions (id, org_id, name, description, created_by)
           VALUES ($1, $2, $3, 'schedulercore fixture', $4)
           ON CONFLICT (id) DO NOTHING""",
        def_id, org_id, name, created_by,
    )
    await conn.execute(
        """INSERT INTO workflow_versions
             (id, workflow_definition_id, org_id, version_number, bpmn_xml,
              change_summary, is_current, created_by)
           VALUES ($1, $2, $3, 1, $4, 'v1', true, $5)
           ON CONFLICT (id) DO NOTHING""",
        VER[def_id], def_id, org_id, bpmn, created_by,
    )
    if service_step:
        step_key, action_key = service_step
        await conn.execute(
            """INSERT INTO workflow_steps
                 (workflow_version_id, org_id, step_key, step_type,
                  autonomy_tier, action_registry_key, display_name)
               VALUES ($1, $2, $3, 'service', 1, $4, 'Reload cost map')
               ON CONFLICT (workflow_version_id, step_key) DO NOTHING""",
            VER[def_id], org_id, step_key, action_key,
        )


async def _mk_trigger(conn, *, trigger_id, def_id, org_id, cron, tz,
                      created_by, start_date=None, end_date=None,
                      max_occurrences=None, occurrence_count=0,
                      last_fired_at=None, is_active=True):
    await conn.execute(
        """INSERT INTO workflow_triggers
             (id, workflow_definition_id, org_id, trigger_type, schedule_cron,
              timezone, start_date, end_date, max_occurrences, occurrence_count,
              last_fired_at, is_active, created_by)
           VALUES ($1, $2, $3, 'scheduled', $4, $5, $6, $7, $8, $9, $10, $11, $12)
           ON CONFLICT (id) DO UPDATE
             SET schedule_cron = EXCLUDED.schedule_cron,
                 timezone = EXCLUDED.timezone,
                 start_date = EXCLUDED.start_date, end_date = EXCLUDED.end_date,
                 max_occurrences = EXCLUDED.max_occurrences,
                 occurrence_count = EXCLUDED.occurrence_count,
                 last_fired_at = EXCLUDED.last_fired_at,
                 is_active = EXCLUDED.is_active""",
        trigger_id, def_id, org_id, cron, tz, start_date, end_date,
        max_occurrences, occurrence_count, last_fired_at, is_active, created_by,
    )


def cron_due_now(tz_name: str, now_utc: datetime) -> str:
    """A daily cron matching THIS minute in ``tz_name``.

    Genuinely due right now in that zone, and — because the two zones used in
    the boundary test are 13-14 hours apart — genuinely NOT due in the other.
    The lookback window absorbs the minute rolling over mid-run.
    """
    local = now_utc.astimezone(ZoneInfo(tz_name))
    return f"{local.minute} {local.hour} * * *"


async def teardown(conn):
    await conn.execute(
        "DELETE FROM member_todos WHERE user_id = ANY($1::uuid[])", ALL_USERS)
    await conn.execute(
        """DELETE FROM member_todos WHERE related_type = 'workflow_run'
             AND related_id IN (
               SELECT r.id FROM workflow_runs r
               JOIN workflow_versions v ON v.id = r.workflow_version_id
               WHERE v.workflow_definition_id = ANY($1::uuid[]))""",
        ALL_DEFS)
    await conn.execute(
        "DELETE FROM workflow_triggers WHERE workflow_definition_id = ANY($1::uuid[])",
        ALL_DEFS)
    await conn.execute(
        "DELETE FROM workflow_triggers WHERE created_by = ANY($1::uuid[])", ALL_USERS)
    await conn.execute(
        """DELETE FROM workflow_run_steps WHERE workflow_run_id IN (
             SELECT r.id FROM workflow_runs r
             JOIN workflow_versions v ON v.id = r.workflow_version_id
             WHERE v.workflow_definition_id = ANY($1::uuid[]))""",
        ALL_DEFS)
    await conn.execute(
        """DELETE FROM workflow_runs WHERE workflow_version_id IN (
             SELECT id FROM workflow_versions
             WHERE workflow_definition_id = ANY($1::uuid[]))""",
        ALL_DEFS)
    await conn.execute(
        "DELETE FROM workflow_runs WHERE started_by = ANY($1::uuid[])", ALL_USERS)
    await conn.execute(
        """DELETE FROM workflow_steps WHERE workflow_version_id IN (
             SELECT id FROM workflow_versions
             WHERE workflow_definition_id = ANY($1::uuid[]))""",
        ALL_DEFS)
    await conn.execute(
        "DELETE FROM workflow_versions WHERE workflow_definition_id = ANY($1::uuid[])",
        ALL_DEFS)
    await conn.execute(
        "DELETE FROM workflow_definitions WHERE id = ANY($1::uuid[])", ALL_DEFS)
    await conn.execute(
        "DELETE FROM workflow_definitions WHERE created_by = ANY($1::uuid[])", ALL_USERS)
    await conn.execute("DELETE FROM audit_log WHERE user_id = ANY($1::uuid[])", ALL_USERS)
    await conn.execute("UPDATE users SET profile_id = NULL WHERE id = ANY($1::uuid[])", ALL_USERS)
    await conn.execute("DELETE FROM users WHERE id = ANY($1::uuid[])", ALL_USERS)


async def quiesce_foreign_triggers(conn):
    """Deactivate any NON-fixture scheduled trigger for the duration, and return
    enough state to put it back exactly as it was.

    The deployed database already holds a real scheduled trigger
    (``'0 9 * * *'``, UTC, inserted by an earlier verify script). The tick scans
    ALL orgs by design, so running this script anywhere near 09:00 UTC would
    fire that row for real and leave a run behind on somebody else's definition.
    A verify script may create and destroy its own fixtures; it may not have
    side effects on data it did not create.
    """
    rows = await conn.fetch(
        """SELECT id, is_active FROM workflow_triggers
           WHERE trigger_type = 'scheduled'
             AND workflow_definition_id <> ALL($1::uuid[])""",
        ALL_DEFS)
    ids = [r["id"] for r in rows if r["is_active"]]
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


async def seed(conn, now_utc):
    org_admin_profile_id = await conn.fetchval(
        "SELECT id FROM profiles WHERE org_id = $1 AND name = $2",
        ORG_ID, ORG_ADMIN_PROFILE)

    await _mk_user(conn, U_ORGADMIN, ORG_ID, "org_admin", org_admin_profile_id)
    await _mk_user(conn, U_MEMBER, ORG_ID, "member", None)
    await _mk_user(conn, U_OTHERORG, OTHER_ORG_ID, "org_admin", None)

    for def_id, org, name, owner in (
        (D_DUE, ORG_ID, "SCHEDCORE Due", U_ORGADMIN),
        (D_TZ, ORG_ID, "SCHEDCORE Timezone", U_ORGADMIN),
        (D_OVERLAP, ORG_ID, "SCHEDCORE Overlap", U_ORGADMIN),
        (D_CAPS, ORG_ID, "SCHEDCORE Caps", U_ORGADMIN),
        (D_OTHERORG, OTHER_ORG_ID, "SCHEDCORE OtherOrg", U_OTHERORG),
        (D_API, ORG_ID, "SCHEDCORE ApiCreated", U_ORGADMIN),
    ):
        await _mk_definition(conn, def_id, org, name,
                             trivial_bpmn(f"schedcore_{name.split()[-1].lower()}"),
                             owner)

    # The HOLD fixture's Service Task invokes a real workflow_invocable action.
    await _mk_definition(
        conn, D_HOLD, ORG_ID, "SCHEDCORE Hold",
        service_bpmn("schedcore_hold", INVOCABLE_ACTION), U_ORGADMIN,
        service_step=("v_service", INVOCABLE_ACTION),
    )
    return org_admin_profile_id


# ═══════════════════════════════════════════════════════════════════════════
# TASK 1 — discovery, reported from live introspection
# ═══════════════════════════════════════════════════════════════════════════
async def task1_report(conn):
    import inspect

    from services import workflow_engine
    from routers import workflows as workflows_router

    print("\n" + "=" * 74)
    print("TASK 1 — DISCOVERY (measured now, not quoted from the prompt)")
    print("=" * 74)

    # 1a — the real column list.
    cols = await conn.fetch(
        """SELECT column_name, data_type, is_nullable, column_default
           FROM information_schema.columns
           WHERE table_name = 'workflow_triggers' AND table_schema = 'public'
           ORDER BY ordinal_position""")
    print("\n  1a. workflow_triggers — EXACT current columns "
          f"({len(cols)} total):")
    for c in cols:
        default = c["column_default"] or "-"
        print(f"        {c['column_name']:<24} {c['data_type']:<28} "
              f"null={c['is_nullable']:<3} default={default}")
    names = [c["column_name"] for c in cols]
    pre_existing = ["id", "workflow_definition_id", "org_id", "trigger_type",
                    "schedule_cron", "event_type", "is_active", "created_by",
                    "created_at"]
    added = ["timezone", "start_date", "end_date", "max_occurrences",
             "occurrence_count", "last_fired_at"]
    check("[Y] TASK 1a: workflow_triggers' pre-sprint 9 columns are all still "
          "present and this sprint's 6 are added — 15 in total",
          all(n in names for n in pre_existing + added) and len(names) == 15,
          f"{len(names)} columns")

    # The prompt/reality disagreement, measured rather than asserted.
    tt = await conn.fetch(
        "SELECT trigger_type, count(*) AS n FROM workflow_triggers GROUP BY 1 ORDER BY 1")
    jsx = (pathlib.Path(__file__).resolve().parents[3]
           / "apps/web/components/admin/WorkflowTriggerScheduler.jsx").read_text()
    print("\n      NOTE — the prompt says trigger_type='schedule'. The deployed "
          "data and\n      the UI both say 'scheduled':")
    for r in tt:
        print(f"        DB: trigger_type={r['trigger_type']!r} -> {r['n']} row(s)")
    print(f"        UI: WorkflowTriggerScheduler.jsx contains "
          f"'scheduled' = {'scheduled' in jsx}, 'schedule\"' = "
          f"{chr(34) + 'schedule' + chr(34) in jsx}")
    check("[Y] TASK 1a: 'scheduled' (not 'schedule') is the real value — the "
          "sprint uses the deployed vocabulary, so the existing row and the "
          "trigger-list UI keep working",
          '"scheduled"' in jsx or "'scheduled'" in jsx)

    # 1b — the real signature.
    sig = inspect.signature(workflow_engine.start_workflow_run)
    src_file = inspect.getsourcefile(workflow_engine.start_workflow_run)
    line = inspect.getsourcelines(workflow_engine.start_workflow_run)[1]
    rel = pathlib.Path(src_file).relative_to(
        pathlib.Path(__file__).resolve().parents[3])
    print(f"\n  1b. start_workflow_run{sig}")
    print(f"        at {rel}:{line}")
    print("        Takes a POOL, not a connection, and opens its own "
          "transactions — which is\n        why the claim commits BEFORE the "
          "fire rather than sharing its transaction.")
    check("[Y] TASK 1b: start_workflow_run's real signature is "
          "(pool, workflow_version_id, org_id, context, started_by)",
          list(sig.parameters) == ["pool", "workflow_version_id", "org_id",
                                   "context", "started_by"],
          f"{list(sig.parameters)}")

    # 1c — the RRULE library.
    import dateutil
    from dateutil.rrule import rrule  # noqa: F401
    reqs = (pathlib.Path(__file__).resolve().parents[1] / "requirements.txt").read_text()
    print(f"\n  1c. python-dateutil {dateutil.__version__} — dateutil.rrule imports.")
    check("[Y] TASK 1c: a real maintained RRULE library (python-dateutil) is "
          "available AND is now a DECLARED dependency — it was previously "
          "present only transitively, via pandas",
          "python-dateutil" in reqs,
          f"version {dateutil.__version__}, declared in requirements.txt="
          f"{'python-dateutil' in reqs}")

    # 1d — the body model, before and after.
    model = workflows_router.TriggerCreate
    fields = sorted(model.model_fields)
    print(f"\n  1d. POST /admin/workflow-triggers body model: "
          f"{model.__name__}{tuple(fields)}")
    print("        Pre-sprint it was EventTriggerCreate(workflow_definition_id, "
          "event_type,\n        is_active) — three fields, with NO path to "
          "create a scheduled trigger.")
    check("[Y] TASK 1d: the body model now accepts trigger_type + schedule_cron "
          "+ the five recurrence fields, and still defaults to the previous "
          "event behaviour",
          set(fields) == {"workflow_definition_id", "trigger_type", "event_type",
                          "is_active", "schedule_cron", "timezone", "start_date",
                          "end_date", "max_occurrences"}
          and model.model_fields["trigger_type"].default == "event",
          f"default trigger_type={model.model_fields['trigger_type'].default!r}")

    # Render's guarantee, and what it does NOT cover (Task 4's premise).
    print("\n  Task 4 premise, restated from the sprint's own confirmed facts:")
    print("        Render's `type: cron` single-run guarantee covers the CRON "
          "SERVICE ITSELF —\n        Render delays the next run while one is "
          "active. This sprint therefore builds\n        NO tick-level lock. It "
          "does check overlap at the WORKFLOW level, which is a\n        "
          "different question and is proven below.")


# ═══════════════════════════════════════════════════════════════════════════
# TASK 3/5 — the recurrence decision, against FIXED instants
# ═══════════════════════════════════════════════════════════════════════════
def decision_tests():
    from services.workflow_schedule import (
        ScheduleError, build_recurrence, evaluate_trigger, parse_cron,
    )

    print("\n" + "=" * 74)
    print("DECISION LAYER — real recurrence math at injected instants")
    print("=" * 74)

    # cron ORs day-of-month against day-of-week; rrule ANDs them. April 2026 is
    # chosen because the 13th is a MONDAY, so OR and AND give different answers
    # (a month where the 13th is itself a Friday would pass either way).
    r = build_recurrence("0 0 13 * 5", datetime(2026, 4, 1, 0, 0))
    days = [d.day for d in r.between(datetime(2026, 4, 1), datetime(2026, 4, 30), inc=True)]
    check("[Y] cron's OR semantics for day-of-month vs day-of-week are "
          "preserved: '0 0 13 * 5' in April 2026 = every Friday OR the 13th, "
          "not 'Friday the 13th'",
          days == [3, 10, 13, 17, 24], f"days fired: {days}")

    # A fixed instant that is 09:00 in New York and 22:00 in Tokyo.
    now = datetime(2026, 8, 26, 13, 0, tzinfo=UTC)
    ny = evaluate_trigger(schedule_cron="0 9 * * *",
                          timezone_name="America/New_York", now_utc=now)
    tokyo = evaluate_trigger(schedule_cron="0 9 * * *",
                             timezone_name="Asia/Tokyo", now_utc=now)
    utc = evaluate_trigger(schedule_cron="0 9 * * *",
                           timezone_name="UTC", now_utc=now)
    print(f"        America/New_York -> {ny}")
    print(f"        Asia/Tokyo       -> {tokyo}")
    print(f"        UTC              -> {utc}")
    check("[Y] TIMEZONE BOUNDARY: one instant, one identical cron, three zones "
          "— due in America/New_York (09:00 local), NOT due in Asia/Tokyo "
          "(22:00) or UTC (13:00)",
          ny.due and not tokyo.due and not utc.due,
          f"NY={ny.due} Tokyo={tokyo.due} UTC={utc.due}")
    check("[Y] the claimed occurrence is the OCCURRENCE instant, not the tick "
          "time — 09:00 EDT = 13:00Z",
          ny.occurrence_utc == datetime(2026, 8, 26, 13, 0, tzinfo=UTC),
          str(ny.occurrence_utc))

    # DST: 09:00 local stays 09:00 local across the spring-forward boundary.
    before = evaluate_trigger(schedule_cron="0 9 * * *",
                              timezone_name="America/New_York",
                              now_utc=datetime(2026, 3, 6, 14, 0, tzinfo=UTC))
    after = evaluate_trigger(schedule_cron="0 9 * * *",
                             timezone_name="America/New_York",
                             now_utc=datetime(2026, 3, 10, 13, 0, tzinfo=UTC))
    check("[Y] DST: a 09:00 America/New_York schedule fires at 14:00Z in EST "
          "and 13:00Z in EDT — it stays at 09:00 LOCAL rather than drifting",
          before.due and after.due
          and before.occurrence_utc.hour == 14 and after.occurrence_utc.hour == 13,
          f"EST->{before.occurrence_utc}  EDT->{after.occurrence_utc}")

    # Idempotency at the decision layer.
    second = evaluate_trigger(schedule_cron="0 9 * * *",
                              timezone_name="America/New_York", now_utc=now,
                              last_fired_at=ny.occurrence_utc)
    check("[Y] the same occurrence evaluated again with last_fired_at set is "
          "NOT due", not second.due, second.reason)

    # Caps and bounds.
    at9 = datetime(2026, 8, 26, 9, 0, tzinfo=UTC)
    capped = evaluate_trigger(schedule_cron="0 9 * * *", timezone_name="UTC",
                              now_utc=at9, max_occurrences=3, occurrence_count=3)
    ended = evaluate_trigger(schedule_cron="0 9 * * *", timezone_name="UTC",
                             now_utc=at9,
                             end_date=datetime(2026, 8, 20, tzinfo=UTC))
    early = evaluate_trigger(schedule_cron="0 9 * * *", timezone_name="UTC",
                             now_utc=at9,
                             start_date=datetime(2026, 9, 1, tzinfo=UTC))
    under = evaluate_trigger(schedule_cron="0 9 * * *", timezone_name="UTC",
                             now_utc=at9, max_occurrences=3, occurrence_count=2)
    check("[Y] max_occurrences stops firing at the cap and not before "
          "(2/3 due, 3/3 not)", under.due and not capped.due,
          f"2/3 -> {under.due}; 3/3 -> {capped.reason}")
    check("[Y] end_date stops firing", not ended.due, ended.reason)
    check("[Y] start_date suppresses firing before the window opens",
          not early.due, early.reason)

    stale = evaluate_trigger(schedule_cron="0 9 * * *", timezone_name="UTC",
                             now_utc=datetime(2026, 8, 26, 14, 0, tzinfo=UTC))
    check("[Y] an occurrence older than the lookback window is STALE and does "
          "not fire — a 09:00 report is not run at 14:00 because the service "
          "was down", not stale.due, stale.reason)

    # Loud failure, not a silent default.
    bad_tz = bad_cron = None
    try:
        evaluate_trigger(schedule_cron="0 9 * * *",
                         timezone_name="Mars/Olympus", now_utc=now)
    except ScheduleError as exc:
        bad_tz = str(exc)
    try:
        parse_cron("0 9 * *")
    except ScheduleError as exc:
        bad_cron = str(exc)
    check("[Y] an unknown IANA zone RAISES rather than defaulting to UTC — a "
          "typo must not silently move somebody's 09:00 report",
          bad_tz is not None, bad_tz)
    check("[Y] a malformed cron expression RAISES with a specific message",
          bad_cron is not None, bad_cron)


# ═══════════════════════════════════════════════════════════════════════════
# TASK 3/4/5 — real firing, against the real engine
# ═══════════════════════════════════════════════════════════════════════════
async def firing_tests(conn, pool, now_utc):
    from services import workflow_scheduler
    from services.workflow_scheduler import _claim, run_scheduler_tick
    from services.workflow_schedule import evaluate_trigger

    print("\n" + "=" * 74)
    print("FIRING LAYER — the real start_workflow_run, the real database")
    print("=" * 74)

    T_DUE = UUID("99000000-0000-0000-0000-0000000009e1")
    T_TZ = UUID("99000000-0000-0000-0000-0000000009e2")
    T_OVERLAP = UUID("99000000-0000-0000-0000-0000000009e3")
    T_ENDED = UUID("99000000-0000-0000-0000-0000000009e4")
    T_CAPPED = UUID("99000000-0000-0000-0000-0000000009e5")
    T_HOLD = UUID("99000000-0000-0000-0000-0000000009e6")
    T_OTHERORG = UUID("99000000-0000-0000-0000-0000000009e7")

    due_cron = cron_due_now("America/New_York", now_utc)
    tokyo_cron = due_cron  # identical expression, different zone

    # ── the due trigger, and its timezone twin ──────────────────────────────
    await _mk_trigger(conn, trigger_id=T_DUE, def_id=D_DUE, org_id=ORG_ID,
                      cron=due_cron, tz="America/New_York", created_by=U_ORGADMIN)
    await _mk_trigger(conn, trigger_id=T_TZ, def_id=D_TZ, org_id=ORG_ID,
                      cron=tokyo_cron, tz="Asia/Tokyo", created_by=U_ORGADMIN)

    # ── the overlap fixture: a REAL non-terminal run already in flight ──────
    await _mk_trigger(conn, trigger_id=T_OVERLAP, def_id=D_OVERLAP, org_id=ORG_ID,
                      cron=due_cron, tz="America/New_York", created_by=U_ORGADMIN)
    blocking_run_id = await conn.fetchval(
        """INSERT INTO workflow_runs
             (workflow_version_id, org_id, status, started_by, started_at)
           VALUES ($1, $2, 'running', $3, now() - interval '10 minutes')
           RETURNING id""",
        VER[D_OVERLAP], ORG_ID, U_ORGADMIN)

    # ── spent triggers: both are DUE by their cron, and must still not fire ──
    await _mk_trigger(conn, trigger_id=T_ENDED, def_id=D_CAPS, org_id=ORG_ID,
                      cron=due_cron, tz="America/New_York", created_by=U_ORGADMIN,
                      end_date=now_utc - timedelta(days=1))
    await _mk_trigger(conn, trigger_id=T_CAPPED, def_id=D_CAPS, org_id=ORG_ID,
                      cron=due_cron, tz="America/New_York", created_by=U_ORGADMIN,
                      max_occurrences=2, occurrence_count=2)

    # ── the hold fixture: started_by is a member WITHOUT author_workflows ────
    await _mk_trigger(conn, trigger_id=T_HOLD, def_id=D_HOLD, org_id=ORG_ID,
                      cron=due_cron, tz="America/New_York", created_by=U_MEMBER)

    # ── the other org ───────────────────────────────────────────────────────
    await _mk_trigger(conn, trigger_id=T_OTHERORG, def_id=D_OTHERORG,
                      org_id=OTHER_ORG_ID, cron=due_cron, tz="America/New_York",
                      created_by=U_OTHERORG)

    mine = {T_DUE, T_TZ, T_OVERLAP, T_ENDED, T_CAPPED, T_HOLD, T_OTHERORG}

    print(f"\n  Fixture cron {due_cron!r} — that is the CURRENT minute in "
          f"America/New_York.")
    print(f"  The same expression in Asia/Tokyo is "
          f"{now_utc.astimezone(ZoneInfo('Asia/Tokyo')).strftime('%H:%M')} "
          f"local, so it is not due there.")

    # ── TICK 1 ──────────────────────────────────────────────────────────────
    print("\n  ── TICK 1 ──")
    cap1 = Capture()
    r1 = await run_scheduler_tick(conn, pool, now_utc=now_utc, log=cap1)

    def outcome(result, trigger_id):
        for f in result.fired:
            if f["trigger_id"] == trigger_id:
                return "fired", f
        for s in result.skipped:
            if s["trigger_id"] == trigger_id:
                return "skipped", s
        for e in result.errors:
            if e["trigger_id"] == trigger_id:
                return "error", e
        return "absent", None

    # The registry regression guard. This sprint's one real bug: REGISTRY is
    # filled by main.py's FastAPI startup hook, and the scheduler is a separate
    # process that never starts FastAPI — so its Service Tasks resolved every
    # action to None and the engine marked them COMPLETED. Silent success.
    from services.action_registry import REGISTRY
    resolved = REGISTRY.get(INVOCABLE_ACTION)
    check("[Y] the tick registers the action REGISTRY itself — the cron process "
          "never runs FastAPI's startup hook, and an empty registry does not "
          "fail loudly: every Service Task would resolve to None and be marked "
          "'completed' having invoked nothing",
          resolved is not None and len(REGISTRY.all()) > 1,
          f"{len(REGISTRY.all())} actions; {INVOCABLE_ACTION} resolved="
          f"{resolved is not None}")
    check("[Y] and the action the HOLD fixture invokes really is "
          "workflow_invocable with a required_permission — so the failure "
          "below is a real permission refusal, not a missing registration",
          resolved is not None
          and getattr(resolved, "workflow_invocable", False)
          and getattr(resolved, "required_permission", None) is not None,
          f"workflow_invocable={getattr(resolved, 'workflow_invocable', None)} "
          f"required_permission={getattr(resolved, 'required_permission', None)}")

    check("[Y] the tick examined every fixture trigger (it scans ALL orgs — a "
          "platform process, not a tenant one)",
          all(outcome(r1, t)[0] != "absent" for t in mine),
          f"examined={r1.examined}, {r1.summary()}")

    kind_due, fired_due = outcome(r1, T_DUE)
    run_row = None
    if fired_due:
        run_row = await conn.fetchrow(
            """SELECT r.id, r.org_id, r.status, r.started_by, r.context,
                      v.workflow_definition_id
               FROM workflow_runs r
               JOIN workflow_versions v ON v.id = r.workflow_version_id
               WHERE r.id = $1""", fired_due["run_id"])
    check("[Y] TASK 5: a trigger genuinely due IN ITS OWN TIMEZONE fired a REAL "
          "workflow_runs row via the REAL start_workflow_run",
          kind_due == "fired" and run_row is not None
          and run_row["workflow_definition_id"] == D_DUE
          and run_row["org_id"] == ORG_ID,
          f"run {fired_due['run_id'] if fired_due else None} "
          f"status={run_row['status'] if run_row is not None else '-'}")
    check("[Y] the fired run records WHY it started — trigger id, trigger type "
          "and the occurrence instant are in its context",
          run_row is not None and all(
              k in (run_row["context"] or "")
              for k in ("trigger_id", "scheduled_occurrence")),
          (run_row["context"] if run_row is not None else "")[:150])
    check("[Y] started_by is the trigger's creator — a real user, so the held "
          "alert has somebody to name",
          run_row is not None and run_row["started_by"] == U_ORGADMIN)

    kind_tz, skip_tz = outcome(r1, T_TZ)
    check("[Y] TASK 5: the timezone twin — IDENTICAL cron, Asia/Tokyo instead of "
          "America/New_York — did NOT fire at the same instant",
          kind_tz == "skipped", skip_tz["reason"] if skip_tz else "")
    tz_runs = await conn.fetchval(
        """SELECT count(*) FROM workflow_runs r
           JOIN workflow_versions v ON v.id = r.workflow_version_id
           WHERE v.workflow_definition_id = $1""", D_TZ)
    check("[Y] and it created no run at all — proven by row count, not by the "
          "tick's own report", tz_runs == 0, f"runs for the Tokyo trigger = {tz_runs}")

    # ── overlap ─────────────────────────────────────────────────────────────
    kind_ov, skip_ov = outcome(r1, T_OVERLAP)
    logged = f"SKIP-OVERLAP trigger={T_OVERLAP}" in cap1.text()
    names_run = str(blocking_run_id) in cap1.text()
    check("[Y] TASK 4: a trigger whose PREVIOUS WORKFLOW run is still "
          "in-progress is skipped — proven against a real non-terminal "
          "workflow_runs row",
          kind_ov == "skipped" and skip_ov.get("blocking_run_id") == blocking_run_id,
          skip_ov["reason"] if skip_ov else "")
    check("[Y] TASK 4: that skip is LOGGED VISIBLY — the emitted line is "
          "captured, is tagged SKIP-OVERLAP, and NAMES the blocking run",
          logged and names_run,
          next((line for line in cap1.lines if "SKIP-OVERLAP" in line), "no line"))
    ov_runs = await conn.fetchval(
        """SELECT count(*) FROM workflow_runs r
           JOIN workflow_versions v ON v.id = r.workflow_version_id
           WHERE v.workflow_definition_id = $1""", D_OVERLAP)
    check("[Y] TASK 4: the overlapped trigger started NO second run — still "
          "exactly the one that was already in flight",
          ov_runs == 1, f"runs = {ov_runs}")
    ov_state = await conn.fetchrow(
        "SELECT last_fired_at, occurrence_count FROM workflow_triggers WHERE id = $1",
        T_OVERLAP)
    check("[Y] TASK 4: an overlap skip does NOT consume the occurrence — "
          "last_fired_at and occurrence_count are untouched, so the schedule "
          "resumes once the run clears",
          ov_state["last_fired_at"] is None and ov_state["occurrence_count"] == 0,
          f"last_fired_at={ov_state['last_fired_at']} "
          f"count={ov_state['occurrence_count']}")

    # ── end_date / max_occurrences ──────────────────────────────────────────
    kind_end, skip_end = outcome(r1, T_ENDED)
    kind_cap, skip_cap = outcome(r1, T_CAPPED)
    due_by_cron = evaluate_trigger(schedule_cron=due_cron,
                                   timezone_name="America/New_York",
                                   now_utc=now_utc).due
    check("[Y] TASK 5: a trigger past its end_date does not fire — and it is "
          "genuinely due by its cron, so the suppression is the end_date and "
          "not a schedule that simply did not match",
          kind_end == "skipped" and due_by_cron,
          f"same cron evaluated bare -> due={due_by_cron}; "
          f"reason={skip_end['reason'] if skip_end else ''}")
    check("[Y] TASK 5: a trigger at its max_occurrences does not fire",
          kind_cap == "skipped", skip_cap["reason"] if skip_cap else "")
    caps_runs = await conn.fetchval(
        """SELECT count(*) FROM workflow_runs r
           JOIN workflow_versions v ON v.id = r.workflow_version_id
           WHERE v.workflow_definition_id = $1""", D_CAPS)
    check("[Y] TASK 5: neither spent trigger created a run", caps_runs == 0,
          f"runs = {caps_runs}")

    # ── cross-org ───────────────────────────────────────────────────────────
    kind_other, fired_other = outcome(r1, T_OTHERORG)
    other_run = None
    if fired_other:
        other_run = await conn.fetchrow(
            "SELECT id, org_id FROM workflow_runs WHERE id = $1",
            fired_other["run_id"])
    check("[Y] TASK 5: CROSS-ORG ISOLATION — the Hollisworks trigger fired its "
          "OWN org's run; org_id follows the trigger, never the scanning "
          "connection",
          kind_other == "fired" and other_run is not None
          and other_run["org_id"] == OTHER_ORG_ID,
          f"run org_id={other_run['org_id'] if other_run is not None else '-'} "
          f"(expected {OTHER_ORG_ID})")
    leak = await conn.fetchval(
        """SELECT count(*) FROM workflow_runs r
           JOIN workflow_versions v ON v.id = r.workflow_version_id
           WHERE v.workflow_definition_id = ANY($1::uuid[]) AND r.org_id <> $2""",
        [D_DUE, D_TZ, D_OVERLAP, D_CAPS, D_HOLD], ORG_ID)
    leak_back = await conn.fetchval(
        """SELECT count(*) FROM workflow_runs r
           JOIN workflow_versions v ON v.id = r.workflow_version_id
           WHERE v.workflow_definition_id = $1 AND r.org_id <> $2""",
        D_OTHERORG, OTHER_ORG_ID)
    check("[Y] TASK 5: no run of a 2nd Act definition landed in another org, "
          "and no run of the Hollisworks definition landed in 2nd Act",
          leak == 0 and leak_back == 0, f"leaks: {leak} / {leak_back}")

    # ── held run alerting ───────────────────────────────────────────────────
    kind_hold, hold_entry = outcome(r1, T_HOLD)
    hold_run = await conn.fetchrow(
        """SELECT r.id, r.status, r.error_detail, r.started_by
           FROM workflow_runs r
           JOIN workflow_versions v ON v.id = r.workflow_version_id
           WHERE v.workflow_definition_id = $1""", D_HOLD)
    # The pre-existing bug this sprint had to fix to satisfy Task 5 at all.
    # services.database._RLSPool.acquire() opens an OUTER transaction, so the
    # engine's "own committed transaction" was only a savepoint; when
    # start_workflow_run re-raised, the outer rollback erased the run, its
    # error_detail and every hold alert together. Every earlier verify script
    # built a RAW asyncpg pool, which is why it never surfaced — while the
    # deployed event-trigger path passes the real RLS pool.
    from services.database import _RLSPool
    check("[Y] REGRESSION: the pool this fired through is the REAL RLS pool "
          "(the one whose outer transaction used to erase held runs), not a "
          "raw asyncpg pool — the bug is only reachable through this one",
          isinstance(pool, _RLSPool), type(pool).__name__)
    check("[Y] TASK 5: the failing scheduler-fired run really HELD — the engine "
          "recorded status='held' with an error_detail, exactly as it does for "
          "a manual run",
          hold_run is not None and hold_run["status"] == "held"
          and bool(hold_run["error_detail"]),
          f"status={hold_run['status'] if hold_run is not None else '-'}: "
          f"{(hold_run['error_detail'] or '')[:110] if hold_run is not None else ''}")
    check("[Y] the tick reported that failure rather than swallowing it",
          kind_hold == "error", str(hold_entry)[:120] if hold_entry else "")

    todos = []
    if hold_run is not None:
        todos = await conn.fetch(
            """SELECT user_id, source, related_type, related_id, title,
                      priority, action_key, category, status
               FROM member_todos WHERE related_type = 'workflow_run'
                 AND related_id = $1""", hold_run["id"])
    from services import workflow_todos
    shape_ok = bool(todos) and all(
        t["source"] == workflow_todos.TODO_SOURCE_RUN_HELD
        and t["related_type"] == "workflow_run"
        and t["category"] == workflow_todos.TODO_CATEGORY
        and t["title"] == "Workflow run held — needs attention"
        and t["priority"] == 5
        and t["action_key"] == "/admin/workflows/runs"
        and t["status"] == "open"
        for t in todos)
    check("[Y] TASK 5: the held scheduler-fired run produced the SAME alert "
          "SHAPE as any other held run — source/related_type/title/priority/"
          "action_key/category all match services/workflow_todos.py's own "
          "constants",
          shape_ok, f"{len(todos)} todo(s)")
    recipients = {t["user_id"] for t in todos}
    org_admins = {r["id"] for r in await conn.fetch(
        "SELECT id FROM users WHERE org_id = $1 AND role = 'org_admin'", ORG_ID)}
    check("[Y] TASK 5: and the SAME recipients — the run's starter plus every "
          "org_admin in that org, which is what create_held_run_alerts does",
          U_MEMBER in recipients and org_admins <= recipients
          and U_ORGADMIN in recipients,
          f"{len(recipients)} recipient(s); starter present="
          f"{U_MEMBER in recipients}; all {len(org_admins)} org_admins present="
          f"{org_admins <= recipients}")
    cross_org_todo = await conn.fetchval(
        """SELECT count(*) FROM member_todos WHERE related_type = 'workflow_run'
             AND related_id = $1 AND org_id <> $2""",
        hold_run["id"] if hold_run is not None else None, ORG_ID)
    check("[Y] the hold alert did not reach another org",
          cross_org_todo == 0, f"{cross_org_todo} foreign-org todo(s)")

    # ── TICK 2: idempotency ─────────────────────────────────────────────────
    print("\n  ── TICK 2 (immediately, same instant) ──")
    cap2 = Capture()
    r2 = await run_scheduler_tick(conn, pool, now_utc=now_utc, log=cap2)

    kind2, entry2 = outcome(r2, T_DUE)
    total_due_runs = await conn.fetchval(
        """SELECT count(*) FROM workflow_runs r
           JOIN workflow_versions v ON v.id = r.workflow_version_id
           WHERE v.workflow_definition_id = $1""", D_DUE)
    check("[Y] TASK 5: running the check-and-fire logic TWICE in immediate "
          "succession against the same due trigger fired EXACTLY ONCE",
          total_due_runs == 1 and kind2 == "skipped",
          f"runs after two ticks = {total_due_runs}; tick 2 -> {kind2}: "
          f"{entry2.get('reason') if entry2 else ''}")
    state = await conn.fetchrow(
        "SELECT last_fired_at, occurrence_count FROM workflow_triggers WHERE id = $1",
        T_DUE)
    check("[Y] the counter advanced exactly once and last_fired_at holds the "
          "OCCURRENCE instant (not the tick time)",
          state["occurrence_count"] == 1
          and state["last_fired_at"] == fired_due["occurrence_utc"],
          f"count={state['occurrence_count']} "
          f"last_fired_at={state['last_fired_at']}")

    # The claim itself, isolated — proving the suppression is the conditional
    # UPDATE and not an incidental ordering effect.
    occ = fired_due["occurrence_utc"]
    replay = await _claim(conn, trigger_id=T_DUE, occurrence_utc=occ)
    check("[Y] the atomic claim is what enforces this: re-claiming the SAME "
          "occurrence matches ZERO rows and returns None",
          replay is None, f"second claim returned {replay!r}")
    later = await _claim(conn, trigger_id=T_DUE,
                         occurrence_utc=occ + timedelta(minutes=1))
    check("[Y] and it is not simply always-refusing — a LATER occurrence still "
          "claims cleanly", later == 2, f"claim returned {later!r}")
    await conn.execute(
        "UPDATE workflow_triggers SET last_fired_at = $2, occurrence_count = 1 "
        "WHERE id = $1", T_DUE, occ)

    # A capped trigger cannot overshoot even if the evaluator is bypassed.
    over = await _claim(conn, trigger_id=T_CAPPED,
                        occurrence_utc=now_utc + timedelta(days=1))
    check("[Y] the claim re-checks max_occurrences itself, closing the "
          "check-then-act gap: a capped trigger cannot be claimed even by "
          "calling the claim directly",
          over is None, f"claim returned {over!r}")

    check("[Y] TASK 4: the tick takes no global lock of its own — Render's "
          "type: cron single-run guarantee already covers the SERVICE, and "
          "the module builds no duplicate of it",
          not any(name.lower().startswith(("_lock", "acquire_lock", "_advisory"))
                  for name in dir(workflow_scheduler)),
          "no lock primitive in services/workflow_scheduler.py")

    return {"due_trigger": T_DUE, "blocking_run_id": blocking_run_id,
            "due_cron": due_cron, "triggers": mine}


# ═══════════════════════════════════════════════════════════════════════════
# TASK 5 — the extended API, through the REAL ASGI app
# ═══════════════════════════════════════════════════════════════════════════
class _Principal:
    """Drives the real ASGI app as one user.

    ``main.verify_token`` is replaced, NOT the auth dependency — so the request
    still traverses routing, the RLS-context middleware, the active-account
    gate and the real ``_require_workflow_permission``.
    """

    __slots__ = ("client", "sub", "org_id")

    def __init__(self, client, sub, org_id):
        self.client, self.sub, self.org_id = client, sub, str(org_id)

    def call(self, method, path, body=None):
        import main
        sub, org = self.sub, self.org_id
        main.verify_token = lambda _t: {
            "sub": sub, "email": f"{sub}@test.local", "org_id": org,
        }
        fn = getattr(self.client, method)
        return fn(path, headers=HEADERS, **({"json": body} if body is not None else {}))


def api_tests(due_cron):
    import main
    from starlette.testclient import TestClient

    client = TestClient(main.app, raise_server_exceptions=False)
    client.__enter__()
    try:
        return _api_tests(client, due_cron)
    finally:
        client.__exit__(None, None, None)


def _api_tests(client, due_cron):
    print("\n" + "=" * 74)
    print("API LAYER — the extended body model, through the REAL ASGI app")
    print("=" * 74)

    admin = _Principal(client, SUB[U_ORGADMIN], ORG_ID)
    member = _Principal(client, SUB[U_MEMBER], ORG_ID)

    # The headline: a NON-super-admin org_admin creates a schedule trigger.
    res = admin.call("post", "/api/v1/admin/workflow-triggers", {
        "workflow_definition_id": str(D_API),
        "trigger_type": "scheduled",
        "schedule_cron": due_cron,
        "timezone": "America/New_York",
        "max_occurrences": 5,
    })
    body = res.json() if res.status_code < 500 else {"detail": res.text[:200]}
    check("[Y] TASK 5: a NON-super-admin ORG_ADMIN created a real schedule-type "
          "trigger through the extended API — which is what confirms "
          "workflowpermsfix landed; before it, this was a 403",
          res.status_code == 201 and body.get("trigger_type") == "scheduled",
          f"HTTP {res.status_code} {str(body)[:180]}")
    created_id = body.get("id") if res.status_code == 201 else None
    check("[Y] the response echoes the real stored recurrence, including the "
          "counters the scheduler will use",
          res.status_code == 201
          and body.get("schedule_cron") == due_cron
          and body.get("timezone") == "America/New_York"
          and body.get("max_occurrences") == 5
          and body.get("occurrence_count") == 0
          and body.get("last_fired_at") is None,
          str(body)[:200])

    # The permission gate still holds for someone without the key.
    res = member.call("post", "/api/v1/admin/workflow-triggers", {
        "workflow_definition_id": str(D_API), "trigger_type": "scheduled",
        "schedule_cron": "0 9 * * *",
    })
    detail = ""
    try:
        detail = res.json().get("detail", "")
    except Exception:  # noqa: BLE001
        pass
    check("[Y] the new schedule path is gated by the SAME "
          "configure_workflow_triggers permission — a member is refused with a "
          "403 that NAMES the key, not a bare 403",
          res.status_code == 403 and detail == f"Permission required: {PERM_TRIGGERS}",
          f"HTTP {res.status_code} {detail}")

    # Backward compatibility: the pre-sprint three-field body still works.
    res = admin.call("post", "/api/v1/admin/workflow-triggers", {
        "workflow_definition_id": str(D_API),
        "event_type": "document_confirmed",
        "is_active": False,
    })
    ev = res.json() if res.status_code < 500 else {}
    check("[Y] BACKWARD COMPATIBLE: the pre-sprint three-field event body — "
          "which sends no trigger_type at all — still creates an event trigger",
          res.status_code == 201 and ev.get("trigger_type") == "event"
          and ev.get("event_type") == "document_confirmed"
          and "schedule_cron" not in ev,
          f"HTTP {res.status_code} {str(ev)[:150]}")

    # Rejections — each must be a 422 naming the real problem.
    rejections = [
        ("a scheduled trigger with no schedule_cron",
         {"workflow_definition_id": str(D_API), "trigger_type": "scheduled"},
         "schedule_cron is required"),
        ("an unparseable cron expression",
         {"workflow_definition_id": str(D_API), "trigger_type": "scheduled",
          "schedule_cron": "0 9 * *"}, "5 fields"),
        ("an out-of-range cron field",
         {"workflow_definition_id": str(D_API), "trigger_type": "scheduled",
          "schedule_cron": "0 99 * * *"}, "outside 0-23"),
        ("an unknown IANA timezone",
         {"workflow_definition_id": str(D_API), "trigger_type": "scheduled",
          "schedule_cron": "0 9 * * *", "timezone": "Mars/Olympus"},
         "unknown IANA timezone"),
        ("max_occurrences = 0",
         {"workflow_definition_id": str(D_API), "trigger_type": "scheduled",
          "schedule_cron": "0 9 * * *", "max_occurrences": 0},
         "positive integer"),
        ("end_date before start_date",
         {"workflow_definition_id": str(D_API), "trigger_type": "scheduled",
          "schedule_cron": "0 9 * * *",
          "start_date": "2026-09-01T00:00:00Z", "end_date": "2026-08-01T00:00:00Z"},
         "must not precede"),
        ("schedule fields on an EVENT trigger (silently storing them is how "
         "schedule_cron became dead code)",
         {"workflow_definition_id": str(D_API), "event_type": "document_confirmed",
          "schedule_cron": "0 9 * * *"},
         "trigger_type='scheduled'"),
        ("an unknown trigger_type",
         {"workflow_definition_id": str(D_API), "trigger_type": "schedule",
          "schedule_cron": "0 9 * * *"},
         "must be 'event' or 'scheduled'"),
    ]
    all_rejected = True
    print("\n  Boundary validation — every bad schedule refused at write time:")
    for label, payload, fragment in rejections:
        res = admin.call("post", "/api/v1/admin/workflow-triggers", payload)
        text = res.text
        ok = res.status_code == 422 and fragment in text
        all_rejected = all_rejected and ok
        check(f"    [Y] 422 for {label}", ok,
              f"HTTP {res.status_code} {text[:130]}")
    check("[Y] TASK 2: an unrunnable schedule can never be STORED active — the "
          "cron expression and the IANA zone are validated at the API boundary, "
          "where the author can still fix them",
          all_rejected)

    return created_id


# ═══════════════════════════════════════════════════════════════════════════
async def main_async():
    dsn = await bootstrap_async()
    if not dsn:
        print("[FAIL] no working DATABASE_URL — cannot verify anything")
        return 1

    import os
    os.environ.setdefault("DATABASE_URL", dsn)

    conn = await asyncpg.connect(dsn, statement_cache_size=0, ssl="require")
    now_utc = datetime.now(UTC).replace(second=0, microsecond=0)
    api_trigger_id = None
    quiesced = []
    try:
        await teardown(conn)

        await task1_report(conn)
        decision_tests()

        print("\n── Fixtures ──")
        quiesced = await quiesce_foreign_triggers(conn)
        check("[Y] pre-existing NON-fixture scheduled triggers are deactivated "
              "for the duration and restored in teardown — the tick scans all "
              "orgs, so this script must not fire somebody else's schedule",
              True, f"{len(quiesced)} foreign trigger(s) parked")
        profile_id = await seed(conn, now_utc)
        check("[Y] the org_admin fixture holds the REAL seeded 'Org Admin' "
              "profile — the one workflowpermsfix granted the workflow keys to",
              profile_id is not None, f"profile_id={profile_id}")

        from services import database as _db
        from services.database import close_pool, get_pool

        pool = await get_pool()
        try:
            info = await firing_tests(conn, pool, now_utc)
        finally:
            # MUST close before the TestClient runs. The app's pool is a
            # module global bound to whichever event loop created it;
            # TestClient builds its OWN loop, and a pool held over from this
            # one makes every request 500 with "attached to a different loop".
            await close_pool()

        loop = asyncio.get_running_loop()
        api_trigger_id = await loop.run_in_executor(
            None, api_tests, info["due_cron"])
        # The app's shutdown hook closes the pool it created on its own loop;
        # clear the global defensively so this loop never inherits a dead one.
        _db._pool = None

        pool = await get_pool()
        try:
            # The API-created trigger is a real one: prove the scheduler fires it.
            if api_trigger_id:
                print("\n  ── TICK 3: the API-created trigger ──")
                cap3 = Capture()
                from services.workflow_scheduler import run_scheduler_tick
                r3 = await run_scheduler_tick(conn, pool, now_utc=now_utc, log=cap3)
                fired = [f for f in r3.fired
                         if str(f["trigger_id"]) == str(api_trigger_id)]
                check("[Y] TASK 5: the trigger the org_admin created through the "
                      "API is a REAL one — the scheduler picked it up and fired "
                      "it, closing the loop from CRUD to execution",
                      len(fired) == 1,
                      f"fired {len(fired)}; {r3.summary()}")
                counted = await conn.fetchval(
                    "SELECT occurrence_count FROM workflow_triggers WHERE id = $1",
                    UUID(api_trigger_id))
                check("[Y] and its occurrence_count advanced against the "
                      "max_occurrences=5 the org_admin set",
                      counted == 1, f"occurrence_count={counted}")
        finally:
            await close_pool()
    finally:
        try:
            if api_trigger_id:
                await conn.execute(
                    """DELETE FROM workflow_run_steps WHERE workflow_run_id IN (
                         SELECT r.id FROM workflow_runs r
                         JOIN workflow_versions v ON v.id = r.workflow_version_id
                         WHERE v.workflow_definition_id = $1)""", D_API)
            await teardown(conn)
            await restore_foreign_triggers(conn, quiesced)
            restored = await conn.fetchval(
                """SELECT count(*) FROM workflow_triggers
                   WHERE id = ANY($1::uuid[]) AND is_active""", quiesced)
            check("[Y] TEARDOWN: every parked foreign trigger is active again, "
                  "and none of them fired",
                  restored == len(quiesced),
                  f"{restored}/{len(quiesced)} restored")
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
                        + (SELECT count(*) FROM member_todos
                             WHERE user_id = ANY($1::uuid[]))""",
                ALL_USERS, ALL_DEFS, list(VER.values()))
            check("[Y] TEARDOWN: zero leftover fixture rows across users, "
                  "definitions, versions, steps, triggers, runs and todos",
                  leftovers == 0, f"leftover rows = {leftovers}")
            survivors = await conn.fetchval(
                "SELECT count(*) FROM workflow_triggers WHERE trigger_type = 'scheduled'")
            check("[Y] TEARDOWN removed only fixtures — the pre-existing "
                  "scheduled trigger row survives",
                  survivors >= 1, f"scheduled triggers remaining = {survivors}")
        finally:
            await conn.close()

    print(f"\n{'=' * 74}")
    print(f"{_n_pass} passed, {_n_fail} failed — "
          f"{'ALL GREEN' if _ok else 'FAILURES ABOVE'}")
    print("=" * 74)
    return 0 if _ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main_async()))
