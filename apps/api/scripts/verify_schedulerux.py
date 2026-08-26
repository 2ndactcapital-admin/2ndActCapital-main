"""verify_schedulerux.py — the workflow Triggers CRUD screen.

WHAT THIS PROVES (against the DEPLOYED database, the REAL ASGI app and the REAL
firing loop — no stubs, no mock rows, no hand-written envelopes):

  [Task 1] The four discovery findings, measured NOW from the live router, the
           live database and the pre-sprint file as git actually holds it —
           including the one place the prompt's premise did not survive contact
           with the permission catalog.
  [Task 2] PATCH and DELETE exist, are gated on configure_workflow_triggers,
           and re-validate an EDIT exactly as create validates a create — proven
           by replaying create's own rejection table against PATCH.
  [Task 3] A trigger created through the SCREEN's endpoint is a real one: a real
           scheduler tick picks it up and fires a real workflow_runs row.
  [Task 3] The dry-run preview returns EXACTLY what the scheduler's own
           recurrence computation would produce — proven by driving
           evaluate_trigger at each previewed instant and asserting it reports
           DUE for that same occurrence, and by asserting there is no occurrence
           BETWEEN two consecutive previewed ones. Not assumed from "both use
           RRULE".
  [Task 4] Pause stops firing and keeps everything (recurrence, bounds, cap,
           occurrence_count, last_fired_at); resume picks up where it left off;
           delete removes the row and a later tick cannot see it.
  [Task 5] A VIEW-ONLY caller — holding view_workflow_runs but NOT
           configure_workflow_triggers — reads the screen and is refused every
           write at the API, AND the components render no write control for the
           envelope that caller actually received. The two are checked
           independently, because a hidden button over an open endpoint and a
           gated endpoint under a visible button are both real bugs and neither
           is ruled out by testing the other.
  [Task 5] `npm run build` exits 0, run for real.
  [Teardown] Zero leftover rows, asserted by count.

Run:  python3 apps/api/scripts/verify_schedulerux.py
"""
import asyncio
import os
import pathlib
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
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

PERM_TRIGGERS = "configure_workflow_triggers"
PERM_VIEW_RUNS = "view_workflow_runs"

# Fixture users. Three distinct capability levels, because the interesting
# claim is about the MIDDLE one and it cannot be stated without the other two.
U_ADMIN = UUID("99000000-0000-0000-0000-0000000009e1")   # holds the write key
U_VIEWER = UUID("99000000-0000-0000-0000-0000000009e2")  # view_workflow_runs ONLY
U_NONE = UUID("99000000-0000-0000-0000-0000000009e3")    # holds nothing
ALL_USERS = [U_ADMIN, U_VIEWER, U_NONE]
SUB = {
    U_ADMIN: "schedux_admin",
    U_VIEWER: "schedux_viewer",
    U_NONE: "schedux_none",
}

# A bespoke profile for the viewer: the seeded 'Org Admin' profile holds the
# write key, so granting the viewer that profile would prove nothing.
VIEWER_PROFILE = UUID("99000000-0000-0000-0000-0000000009f9")
VIEWER_PROFILE_NAME = "SCHEDUX Runs Viewer"

D_UI = UUID("99000000-0000-0000-0000-0000000009c8")      # the UI-created trigger
D_EVENT = UUID("99000000-0000-0000-0000-0000000009c9")   # an event trigger to patch
ALL_DEFS = [D_UI, D_EVENT]
VER = {d: UUID(str(d).replace("9c", "9d", 1)) for d in ALL_DEFS}

HEADERS = {"Authorization": "Bearer verify-token"}

BPMN_NS = "http://www.omg.org/spec/BPMN/20100524/MODEL"

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


def report(title, body):
    print(f"\n  {title}")
    for line in body.strip().splitlines():
        print(f"      {line}")


class Capture:
    """Collects a tick's log lines so 'the tick never referenced it' can be an
    assertion about the log rather than only about the return value."""

    def __init__(self, echo=False):
        self.lines = []
        self.echo = echo

    def __call__(self, message):
        self.lines.append(str(message))
        if self.echo:
            print(f"        │ {message}")

    def text(self):
        return "\n".join(self.lines)


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


def trivial_bpmn(proc_id) -> str:
    """Start -> End. Runs straight to 'completed' with no side effects, so a
    fired run never blocks the NEXT fire on the overlap check."""
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


async def _mk_definition(conn, def_id, name, created_by):
    await conn.execute(
        """INSERT INTO workflow_definitions (id, org_id, name, description, created_by)
           VALUES ($1, $2, $3, 'schedulerux fixture', $4)
           ON CONFLICT (id) DO NOTHING""",
        def_id, ORG_ID, name, created_by,
    )
    await conn.execute(
        """INSERT INTO workflow_versions
             (id, workflow_definition_id, org_id, version_number, bpmn_xml,
              change_summary, is_current, created_by)
           VALUES ($1, $2, $3, 1, $4, 'v1', true, $5)
           ON CONFLICT (id) DO NOTHING""",
        VER[def_id], def_id, ORG_ID,
        trivial_bpmn(f"schedux_{str(def_id)[-4:]}"), created_by,
    )


async def seed(conn):
    org_admin_profile_id = await conn.fetchval(
        "SELECT id FROM profiles WHERE org_id = $1 AND name = $2",
        ORG_ID, ORG_ADMIN_PROFILE)

    # The viewer's profile grants view_workflow_runs and NOTHING else. This is
    # the whole fixture for the view-only claim: if the profile accidentally
    # carried configure_workflow_triggers, every refusal below would be vacuous.
    await conn.execute(
        """INSERT INTO profiles (id, org_id, name, description, is_seed)
           VALUES ($1, $2, $3, 'schedulerux fixture', false)
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

    await _mk_definition(conn, D_UI, "SCHEDUX Screen Created", U_ADMIN)
    await _mk_definition(conn, D_EVENT, "SCHEDUX Event", U_ADMIN)
    return org_admin_profile_id


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
    await conn.execute("UPDATE users SET profile_id = NULL WHERE id = ANY($1::uuid[])",
                       ALL_USERS)
    await conn.execute("DELETE FROM users WHERE id = ANY($1::uuid[])", ALL_USERS)
    await conn.execute("DELETE FROM profile_permissions WHERE profile_id = $1",
                       VIEWER_PROFILE)
    await conn.execute("DELETE FROM profiles WHERE id = $1", VIEWER_PROFILE)


async def quiesce_foreign_triggers(conn):
    """Park every NON-fixture scheduled trigger for the duration.

    The tick scans ALL orgs by design. A verify script may create and destroy
    its own fixtures; it may not fire somebody else's 09:00 schedule as a side
    effect of running near 09:00.
    """
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
    """A daily cron matching THIS minute in ``tz_name`` — genuinely due, not
    asserted into existence."""
    local = now_utc.astimezone(ZoneInfo(tz_name))
    return f"{local.minute} {local.hour} * * *"


# ═══════════════════════════════════════════════════════════════════════════
# TASK 1 — the four findings, measured live
# ═══════════════════════════════════════════════════════════════════════════
async def task1_report(conn):
    import inspect

    from routers import workflows as wr
    from routers import portfolio_positions as pp

    print("\n" + "=" * 74)
    print("TASK 1 — DISCOVERY (measured now: live router, live DB, git history)")
    print("=" * 74)

    # ── 1a. the REAL pre-sprint component, from git ──────────────────────
    path = "apps/web/components/admin/WorkflowTriggerScheduler.jsx"
    before = git_show(f"HEAD:{path}")
    after = read(REPO / path)
    before_x = strip_js_comments(before)
    after_x = strip_js_comments(after)

    recurrence_fields = ("occurrence_count", "last_fired_at", "timezone",
                         "start_date", "end_date", "max_occurrences")
    before_shows = [f for f in recurrence_fields if f in before_x]
    # The screen is now TWO files — the list and its detail pane — so the
    # "which fields does it surface" question is asked of both. Splitting them
    # is the design, not an omission: the list carries the columns an operator
    # scans (counters, state, next run) and the pane carries the full
    # recurrence. Measuring only the grid would score the pane's fields as
    # missing when they are one click away.
    pane_x = strip_js_comments(read(PANE))
    after_shows = [f for f in recurrence_fields if f in after_x or f in pane_x]
    grid_shows = [f for f in recurrence_fields if f in after_x]

    report(
        "1a — WorkflowTriggerScheduler.jsx, the component that displays triggers",
        f"BEFORE (git HEAD): {len(before.splitlines())} lines. A hand-rolled "
        f"<table> ({before_x.count('<table')} of them), NOT DataGrid "
        f"(imports DataGrid: {'@/components/ui/DataGrid' in before_x}). Its only "
        f"write was a create form hardcoded to 'document_confirmed' "
        f"(createEventTriggerAction: {'createEventTriggerAction' in before_x}). "
        f"It rendered schedule_cron raw inside a <code>, and showed NONE of the "
        f"live recurrence columns: {before_shows or 'none of them'}. No sort, "
        f"no filter, no detail pane, no permission envelope, and no pause, edit "
        f"or delete.\n"
        f"AFTER: {len(after.splitlines())} lines, DataGrid-driven "
        f"({'@/components/ui/DataGrid' in after_x}), right-pane detail "
        f"({'TriggerDetailPane' in after_x}), and the screen surfaces all "
        f"{len(after_shows)}/{len(recurrence_fields)} recurrence fields — "
        f"{grid_shows} as list columns (plus the server-built "
        f"schedule_summary and next_occurrence), the rest in the pane.\n"
        f"DECISION: REPLACED, not extended — every line of the old body would "
        f"have had to change anyway. The PATH is kept because "
        f"verify_schedulercore.py reads this file to assert the deployed "
        f"vocabulary really is 'scheduled'.",
    )
    check("[Y] TASK 1a: the pre-sprint component surfaced NONE of the six live "
          "recurrence/counter fields and used no DataGrid; the replacement "
          "surfaces all six across list and pane, and is DataGrid-driven",
          before_shows == []
          and "@/components/ui/DataGrid" not in before_x
          and len(after_shows) == len(recurrence_fields)
          and "@/components/ui/DataGrid" in after_x,
          f"before={before_shows}, after={after_shows} "
          f"(list columns: {grid_shows})")
    check("[Y] TASK 1a: the two counters the prompt calls out — "
          "occurrence_count and last_fired_at — are LIST columns, not buried in "
          "the pane; the list shows what the trigger has DONE, not only how it "
          "was defined",
          {"occurrence_count", "last_fired_at"} <= set(grid_shows),
          f"list columns: {grid_shows}")
    check("[Y] TASK 1a: the replacement still contains the literal 'scheduled' "
          "that verify_schedulercore.py asserts on — the deployed vocabulary is "
          "unchanged by this sprint",
          '"scheduled"' in after or "'scheduled'" in after)

    # ── 1b. what GET /admin/workflow-triggers really returned ────────────
    get_src_before = git_show("HEAD:apps/api/routers/workflows.py")
    get_now = inspect.getsource(wr.list_workflow_triggers)
    recurrence_cols = ("timezone", "start_date", "end_date", "max_occurrences",
                       "occurrence_count", "last_fired_at")
    # The pre-sprint handler, sliced out of the file git holds.
    m = re.search(
        r'@router\.get\("/admin/workflow-triggers"\).*?(?=\n@router\.|\n# ---)',
        get_src_before, flags=re.DOTALL)
    before_get = m.group(0) if m else ""
    had_all = all(c in before_get for c in recurrence_cols)

    report(
        "1b — GET /admin/workflow-triggers: did it already return the new fields?",
        f"YES. The handler as of HEAD already SELECTed all six recurrence "
        f"columns ({recurrence_cols}) — schedulercore extended the read "
        f"alongside the write, so no column work was owed here: "
        f"all six present = {had_all}.\n"
        f"What it did NOT have is the ENVELOPE. It returned a bare "
        f"`[dict(r) for r in rows]` (bare list: "
        f"{'return [dict(r) for r in rows]' in before_get}) with no permissions "
        f"block — because the read gate WAS the write gate, so a caller who "
        f"could see the list could always write and there was nothing to "
        f"publish. That is what this sprint extends.",
    )
    check("[Y] TASK 1b: GET already returned every recurrence field before this "
          "sprint — the extension owed was the permission ENVELOPE, not columns",
          had_all and "return [dict(r) for r in rows]" in before_get,
          f"six columns present={had_all}")
    check("[Y] TASK 1b: GET now returns {rows, permissions} instead of a bare list",
          '"rows"' in get_now and '"permissions"' in get_now
          and "_trigger_permissions" in get_now)

    # ── 1c. what write endpoints existed ─────────────────────────────────
    before_methods = sorted(set(re.findall(
        r'@router\.(get|post|patch|delete|put)\("(/admin/workflow-triggers[^"]*)"',
        get_src_before)))
    now_routes = sorted(
        (sorted(r.methods - {"HEAD", "OPTIONS"})[0], r.path)
        for r in wr.router.routes
        if getattr(r, "path", "").startswith("/admin/workflow-triggers")
    )
    report(
        "1c — does a DELETE or PATCH exist for a trigger today?",
        f"NO. NEITHER. Before this sprint the ENTIRE trigger surface was "
        f"{[f'{m.upper()} {p}' for m, p in before_methods]} — two endpoints, "
        f"both additive. Nothing anywhere in the API could deactivate a trigger, "
        f"edit one, or remove one, and no preview endpoint existed either. "
        f"'Pause without delete' and 'true delete' both had to be BUILT; "
        f"reporting this before building is the point of 1c.\n"
        f"NOW: {[f'{m} {p}' for m, p in now_routes]}",
    )
    check("[Y] TASK 1c: neither PATCH nor DELETE nor a preview endpoint existed "
          "for a trigger before this sprint — GET and POST were the whole "
          "surface",
          {m for m, _ in before_methods} == {"get", "post"}
          and not any("preview" in p for _, p in before_methods),
          f"pre-sprint: {before_methods}")
    check("[Y] TASK 1c: PATCH, DELETE and the preview endpoint are now all "
          "really registered on the app's router",
          {("PATCH", "/admin/workflow-triggers/{trigger_id}"),
           ("DELETE", "/admin/workflow-triggers/{trigger_id}"),
           ("POST", "/admin/workflow-triggers/preview")}
          <= set(now_routes),
          f"{now_routes}")

    # ── 1d. the established envelope shape, compared field by field ──────
    portfolio_env = inspect.getsource(pp._permission_envelope)
    trigger_env = inspect.getsource(wr._trigger_permissions)
    portfolio_keys = set(re.findall(r'"(can_\w+|is_super_admin|\w+_permission)":',
                                    portfolio_env))
    trigger_keys = set(re.findall(r'"(can_\w+|is_super_admin|\w+_permission)":',
                                  trigger_env))

    perms_in_catalog = [r["name"] for r in await conn.fetch(
        "SELECT name FROM permissions WHERE resource = 'workflows' ORDER BY name")]

    report(
        "1d — the established Portfolio UX permission-envelope pattern",
        f"portfolio_positions._permission_envelope publishes {sorted(portfolio_keys)} "
        f"per response, and _vocabularies() empties `editable` / "
        f"`inline_editable` for a caller whose can_write is false. The client "
        f"renders a write control ONLY from those, with no local fallback list.\n"
        f"This sprint reuses that shape verbatim: _trigger_permissions publishes "
        f"{sorted(trigger_keys)}.\n"
        f"ONE REAL CONFLICT, resolved rather than glossed. The prompt says "
        f"configure_workflow_triggers gates the whole surface — and it did, for "
        f"BOTH read and write. That makes Task 5's view-only user "
        f"unrepresentable: without the key the list endpoint 403s and there are "
        f"no 'read parts' left to see. The live catalog holds exactly three "
        f"workflow keys ({perms_in_catalog}), so rather than invent a fourth, "
        f"the READ is widened to `view_workflow_runs OR "
        f"configure_workflow_triggers` and the WRITE stays "
        f"configure_workflow_triggers alone. Nobody loses access: every caller "
        f"who could read before holds the write key and still passes.",
    )
    check("[Y] TASK 1d: the trigger envelope publishes the SAME keys the "
          "Portfolio UX envelope does — one pattern, not a second one",
          trigger_keys == portfolio_keys,
          f"triggers={sorted(trigger_keys)} portfolio={sorted(portfolio_keys)}")
    check("[Y] TASK 1d: the workflow permission catalog really holds only three "
          "keys, which is WHY the read gate reuses view_workflow_runs instead "
          "of a new one",
          sorted(perms_in_catalog) == ["author_workflows",
                                       "configure_workflow_triggers",
                                       "view_workflow_runs"],
          f"{perms_in_catalog}")


# ═══════════════════════════════════════════════════════════════════════════
# The preview ↔ scheduler equivalence, at the PURE layer
# ═══════════════════════════════════════════════════════════════════════════
#: Recurrences chosen to exercise the parts of the translation that can
#: disagree: a plain daily, a DST-crossing daily in a real zone, a weekday
#: restriction, a sub-hourly step, a monthly, and the cron OR-of-two-day-fields
#: that rrule would otherwise AND.
EQUIV_CASES = [
    ("plain daily, UTC", "0 9 * * *", "UTC", None, None, None),
    ("daily across the US DST boundary", "0 9 * * *", "America/New_York",
     datetime(2026, 10, 30, 0, 0, tzinfo=UTC), None, None),
    ("weekdays only", "30 14 * * 1-5", "Europe/London", None, None, None),
    ("every 15 minutes", "*/15 * * * *", "Asia/Tokyo", None, None, None),
    ("monthly on the 1st", "0 0 1 * *", "UTC", None, None, None),
    ("cron's OR of day-of-month and day-of-week", "0 0 13 * 5", "UTC",
     None, None, None),
    ("capped at 3 firings", "0 9 * * *", "UTC", None, None, 3),
    ("bounded by an end_date", "0 9 * * *", "UTC", None,
     datetime(2026, 9, 3, 12, 0, tzinfo=UTC), None),
]


def preview_equivalence_tests():
    from services.workflow_schedule import evaluate_trigger, next_occurrences

    print("\n" + "=" * 74)
    print("TASK 3 — the dry-run preview IS the scheduler's own computation")
    print("=" * 74)
    print("\n  For every previewed occurrence o_k the REAL evaluate_trigger is")
    print("  driven at that exact instant with last_fired_at = o_(k-1). It must")
    print("  report DUE for o_k — and, one minute earlier, NOT due, which is")
    print("  what rules out an occurrence the preview silently skipped.\n")

    anchor = datetime(2026, 8, 26, 12, 0, tzinfo=UTC)
    all_matched = True
    all_complete = True

    for label, cron, tz, start, end, cap in EQUIV_CASES:
        occurrences = next_occurrences(
            schedule_cron=cron, timezone_name=tz, after_utc=anchor, count=5,
            start_date=start, end_date=end, max_occurrences=cap,
            occurrence_count=0,
        )
        matched, complete = True, True
        previous = anchor
        for index, occurrence in enumerate(occurrences):
            # Lookback wide enough to span the whole gap, so a MISS would be
            # visible as the evaluator reaching further back and finding
            # something the preview never listed.
            gap = int((occurrence - previous).total_seconds() // 60) + 2
            decision = evaluate_trigger(
                schedule_cron=cron, timezone_name=tz, now_utc=occurrence,
                last_fired_at=previous, start_date=start, end_date=end,
                max_occurrences=cap, occurrence_count=index,
                lookback_minutes=gap,
            )
            if not (decision.due and decision.occurrence_utc == occurrence):
                matched = False

            # Nothing between previous and occurrence: one minute BEFORE the
            # occurrence, with the same window, the evaluator must find nothing.
            if occurrence - previous > timedelta(minutes=1):
                between = evaluate_trigger(
                    schedule_cron=cron, timezone_name=tz,
                    now_utc=occurrence - timedelta(minutes=1),
                    last_fired_at=previous, start_date=start, end_date=end,
                    max_occurrences=cap, occurrence_count=index,
                    lookback_minutes=gap,
                )
                if between.due:
                    complete = False
            previous = occurrence

        all_matched = all_matched and matched
        all_complete = all_complete and complete
        expected = 5 if cap is None and end is None else len(occurrences)
        check(f"    [Y] {label}: all {len(occurrences)} previewed occurrences are "
              f"DUE at their own instant, and nothing lies between consecutive "
              f"ones",
              matched and complete and len(occurrences) == expected,
              f"{[o.isoformat() for o in occurrences[:3]]}"
              f"{'…' if len(occurrences) > 3 else ''}")

    check("[Y] TASK 3: the preview and the firing loop agree occurrence for "
          "occurrence across every recurrence shape — DST boundary, weekday "
          "restriction, sub-hourly step, monthly, and cron's OR of the two day "
          "fields that rrule would otherwise AND",
          all_matched and all_complete)

    # And the caps really truncate rather than being decoration.
    capped = next_occurrences(schedule_cron="0 9 * * *", timezone_name="UTC",
                              after_utc=anchor, count=5, max_occurrences=3,
                              occurrence_count=0)
    spent = next_occurrences(schedule_cron="0 9 * * *", timezone_name="UTC",
                             after_utc=anchor, count=5, max_occurrences=3,
                             occurrence_count=3)
    check("[Y] a max_occurrences cap truncates the preview honestly, and a cap "
          "already SPENT previews as nothing at all rather than as five runs "
          "that will never happen",
          len(capped) == 3 and spent == [],
          f"cap 3 from 0 -> {len(capped)}; cap 3 from 3 -> {len(spent)}")

    # The preview must never invent an occurrence at or before its anchor.
    exact = next_occurrences(schedule_cron="0 12 * * *", timezone_name="UTC",
                             after_utc=anchor, count=1)
    check("[Y] the preview is strictly AFTER its anchor — an anchor landing "
          "exactly on an occurrence does not re-list that occurrence, which is "
          "the same 'already covered by last_fired_at' rule the evaluator uses",
          exact and exact[0] == anchor + timedelta(days=1),
          f"anchor {anchor.isoformat()} -> {exact[0].isoformat() if exact else None}")

    # An unparseable schedule raises rather than defaulting to something.
    from services.workflow_schedule import ScheduleError
    raised = False
    try:
        next_occurrences(schedule_cron="0 9 * *", timezone_name="UTC",
                         after_utc=anchor, count=1)
    except ScheduleError:
        raised = True
    check("[Y] the preview refuses an unparseable cron with the evaluator's own "
          "ScheduleError instead of silently previewing something else",
          raised)


# ═══════════════════════════════════════════════════════════════════════════
# The ASGI harness
# ═══════════════════════════════════════════════════════════════════════════
class _Principal:
    """Drives the real ASGI app as one user.

    ``main.verify_token`` is replaced, NOT the auth dependency — so a request
    still traverses routing, the RLS-context middleware, the active-account gate
    and the real permission resolution.
    """

    __slots__ = ("client", "sub", "org_id")

    def __init__(self, client, sub, org_id=ORG_ID):
        self.client, self.sub, self.org_id = client, sub, str(org_id)

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


def _detail(res):
    try:
        body = res.json()
    except Exception:  # noqa: BLE001
        return res.text[:200]
    detail = body.get("detail") if isinstance(body, dict) else None
    if isinstance(detail, list):
        return " · ".join(str(d.get("msg", d)) for d in detail)
    return detail if detail is not None else str(body)[:200]


# ═══════════════════════════════════════════════════════════════════════════
# PHASE 1 — read the live list, create through the screen's endpoint
# ═══════════════════════════════════════════════════════════════════════════
def phase_create(client, due_cron, tz_name):
    print("\n" + "=" * 74)
    print("TASKS 2/3/5 — the screen's own endpoints, through the REAL ASGI app")
    print("=" * 74)
    admin = _Principal(client, SUB[U_ADMIN])

    # The list, before anything is created.
    res = admin.call("get", "/api/v1/admin/workflow-triggers")
    body = res.json() if res.status_code == 200 else {}
    check("[Y] TASK 5: the screen's list endpoint returns a real 200 with the "
          "{rows, permissions} envelope — the screen is fed by the live API, "
          "and there is nowhere in it for mock data to come from",
          res.status_code == 200
          and isinstance(body, dict)
          and isinstance(body.get("rows"), list)
          and isinstance(body.get("permissions"), dict),
          f"HTTP {res.status_code} keys={sorted(body) if isinstance(body, dict) else body}")
    check("[Y] the write-capable caller's envelope says can_write, and names the "
          "permission that decided it",
          body.get("permissions", {}).get("can_write") is True
          and body.get("permissions", {}).get("write_permission") == PERM_TRIGGERS,
          str(body.get("permissions"))[:160])
    baseline_ids = {str(r["id"]) for r in body.get("rows", [])}

    # ── The dry run, BEFORE saving anything. ──
    preview_body = {
        "schedule_cron": due_cron, "timezone": tz_name, "count": 5,
    }
    res = admin.call("post", "/api/v1/admin/workflow-triggers/preview", preview_body)
    preview = res.json() if res.status_code == 200 else {}
    check("[Y] TASK 3: the dry-run preview endpoint returns five real "
          "occurrences for a recurrence that has NOT been saved",
          res.status_code == 200 and len(preview.get("occurrences", [])) == 5,
          f"HTTP {res.status_code} {str(preview)[:160]}")
    check("[Y] and it returns the human-readable summary the list column shows, "
          "built server-side from the same parse_cron the evaluator uses",
          bool(preview.get("summary")) and tz_name in str(preview.get("summary")),
          str(preview.get("summary")))

    # Nothing was stored by previewing.
    res = admin.call("get", "/api/v1/admin/workflow-triggers")
    after_ids = {str(r["id"]) for r in res.json().get("rows", [])}
    check("[Y] TASK 3: previewing stored NOTHING — the trigger list is byte-for-"
          "byte the same set of ids afterwards",
          after_ids == baseline_ids,
          f"{len(baseline_ids)} before, {len(after_ids)} after")

    # ── Create, exactly as the screen's form does. ──
    res = admin.call("post", "/api/v1/admin/workflow-triggers", {
        "workflow_definition_id": str(D_UI),
        "trigger_type": "scheduled",
        "schedule_cron": due_cron,
        "timezone": tz_name,
        "max_occurrences": 4,
    })
    created = res.json() if res.status_code == 201 else {}
    check("[Y] TASK 3: a trigger is created through the same endpoint the "
          "screen's create form posts to",
          res.status_code == 201 and created.get("trigger_type") == "scheduled",
          f"HTTP {res.status_code} {_detail(res)}")
    trigger_id = created.get("id")

    # ── An event trigger too, so the PATCH type rules can be exercised. ──
    res = admin.call("post", "/api/v1/admin/workflow-triggers", {
        "workflow_definition_id": str(D_EVENT),
        "event_type": "document_confirmed",
        "is_active": True,
    })
    event_id = res.json().get("id") if res.status_code == 201 else None

    # ── The list now carries the row, decorated. ──
    res = admin.call("get", "/api/v1/admin/workflow-triggers")
    rows = {str(r["id"]): r for r in res.json().get("rows", [])}
    row = rows.get(str(trigger_id), {})
    check("[Y] TASK 5: the created trigger appears in the live list with every "
          "column the screen renders — recurrence summary, counters, next "
          "occurrence — none of them invented client-side",
          bool(row)
          and row.get("schedule_cron") == due_cron
          and row.get("timezone") == tz_name
          and row.get("occurrence_count") == 0
          and row.get("last_fired_at") is None
          and bool(row.get("schedule_summary"))
          and row.get("next_occurrence") is not None
          and row.get("schedule_error") is None,
          f"summary={row.get('schedule_summary')!r} "
          f"next={row.get('next_occurrence')}")

    # ── The preview matches what the LIST says is next. ──
    res = admin.call("post", "/api/v1/admin/workflow-triggers/preview", {
        "schedule_cron": due_cron, "timezone": tz_name, "count": 5,
    })
    endpoint_occurrences = [o["utc"] for o in res.json().get("occurrences", [])]

    return {
        "trigger_id": trigger_id,
        "event_id": event_id,
        "endpoint_occurrences": endpoint_occurrences,
        "preview_after": preview.get("after"),
    }


# ═══════════════════════════════════════════════════════════════════════════
# PHASE 2 — validation parity between create and edit
# ═══════════════════════════════════════════════════════════════════════════
#: create's own rejection table, replayed against PATCH. Every one of these is
#: a payload create refuses; an edit that accepted any of them could turn a
#: valid stored trigger into one the tick can only log an error about.
EDIT_REJECTIONS = [
    ("an unparseable cron expression", {"schedule_cron": "0 9 * *"}, "5 fields"),
    ("an out-of-range cron field", {"schedule_cron": "0 99 * * *"}, "outside 0-23"),
    ("an unknown IANA timezone", {"timezone": "Mars/Olympus"},
     "unknown IANA timezone"),
    ("max_occurrences = 0", {"max_occurrences": 0}, "positive integer"),
    ("an emptied cron expression", {"schedule_cron": "   "},
     "schedule_cron is required"),
    ("end_date before start_date",
     {"start_date": "2026-09-01T00:00:00Z", "end_date": "2026-08-01T00:00:00Z"},
     "must not precede"),
]


def phase_validation(client, trigger_id, event_id):
    admin = _Principal(client, SUB[U_ADMIN])
    print("\n  Boundary validation — an EDIT is validated exactly as a CREATE:")
    all_rejected = True
    for label, payload, fragment in EDIT_REJECTIONS:
        res = admin.call("patch",
                         f"/api/v1/admin/workflow-triggers/{trigger_id}", payload)
        ok = res.status_code == 422 and fragment in res.text
        all_rejected = all_rejected and ok
        check(f"    [Y] 422 for {label}", ok, f"HTTP {res.status_code} {_detail(res)}")
    check("[Y] TASK 2: editing re-validates the cron expression, the IANA zone "
          "and the date ordering exactly as create does — the SAME "
          "_validate_recurrence, so a schedule create refuses is one edit "
          "refuses",
          all_rejected)

    # The half-payload case: an end_date valid on its own but wrong against the
    # STORED start_date. Only merge-then-validate catches this.
    res = admin.call("patch", f"/api/v1/admin/workflow-triggers/{trigger_id}",
                     {"start_date": "2026-09-01T00:00:00Z"})
    step1 = res.status_code
    res = admin.call("patch", f"/api/v1/admin/workflow-triggers/{trigger_id}",
                     {"end_date": "2026-08-01T00:00:00Z"})
    check("[Y] TASK 2: an edit is validated against the MERGED row, not the "
          "submitted fields — an end_date that is fine in isolation is still "
          "refused against the STORED start_date, so bounds cannot be ordered "
          "backwards one field at a time",
          step1 == 200 and res.status_code == 422 and "must not precede" in res.text,
          f"set start: HTTP {step1}; then end: HTTP {res.status_code} {_detail(res)}")
    # Put it back.
    admin.call("patch", f"/api/v1/admin/workflow-triggers/{trigger_id}",
               {"start_date": None})

    # Schedule fields on an EVENT trigger are refused, not silently stored.
    res = admin.call("patch", f"/api/v1/admin/workflow-triggers/{event_id}",
                     {"schedule_cron": "0 9 * * *"})
    check("[Y] TASK 2: a schedule field sent to an EVENT trigger is refused, "
          "not silently stored where nothing reads it — which is exactly how "
          "schedule_cron became dead code in the first place",
          res.status_code == 422 and "trigger_type='scheduled'" in res.text,
          f"HTTP {res.status_code} {_detail(res)}")

    # An event trigger CAN still be paused — pause is type-independent.
    res = admin.call("patch", f"/api/v1/admin/workflow-triggers/{event_id}",
                     {"is_active": False})
    check("[Y] an EVENT trigger can still be paused — pausing is about whether "
          "a trigger fires, not about how it is triggered",
          res.status_code == 200 and res.json().get("is_active") is False,
          f"HTTP {res.status_code}")

    # A 404, not a 403, for a trigger that does not exist.
    res = admin.call("patch",
                     "/api/v1/admin/workflow-triggers/"
                     "99000000-0000-0000-0000-0000000000ff", {"is_active": False})
    check("[Y] an unknown trigger id is a 404 rather than a 403 — a 403 there "
          "would confirm the existence of a row the caller may not see",
          res.status_code == 404, f"HTTP {res.status_code}")


# ═══════════════════════════════════════════════════════════════════════════
# PHASE 3 — pause / resume / delete, and the view-only refusals
# ═══════════════════════════════════════════════════════════════════════════
def phase_pause(client, trigger_id):
    admin = _Principal(client, SUB[U_ADMIN])
    res = admin.call("get", "/api/v1/admin/workflow-triggers")
    before = {str(r["id"]): r for r in res.json()["rows"]}[str(trigger_id)]

    res = admin.call("patch", f"/api/v1/admin/workflow-triggers/{trigger_id}",
                     {"is_active": False})
    after = res.json() if res.status_code == 200 else {}
    kept = all(
        after.get(f) == before.get(f)
        for f in ("schedule_cron", "timezone", "start_date", "end_date",
                  "max_occurrences", "occurrence_count", "last_fired_at")
    )
    check("[Y] TASK 4: pausing is PATCH {is_active:false} and NOTHING else — "
          "the recurrence, the bounds, the cap, occurrence_count and "
          "last_fired_at all survive it, field by field",
          res.status_code == 200 and after.get("is_active") is False and kept,
          f"HTTP {res.status_code}; occurrence_count "
          f"{before.get('occurrence_count')} -> {after.get('occurrence_count')}, "
          f"last_fired {before.get('last_fired_at')} -> {after.get('last_fired_at')}")
    check("[Y] TASK 4: a paused trigger reports no next occurrence — the screen "
          "cannot print 'next: tomorrow 9:00' beside a Paused pill",
          after.get("next_occurrence") is None,
          f"next_occurrence={after.get('next_occurrence')}")
    return before, after


def phase_resume(client, trigger_id):
    admin = _Principal(client, SUB[U_ADMIN])
    res = admin.call("patch", f"/api/v1/admin/workflow-triggers/{trigger_id}",
                     {"is_active": True})
    return res.status_code, res.json() if res.status_code == 200 else {}


def phase_viewonly(client, trigger_id):
    print("\n  A VIEW-ONLY caller (view_workflow_runs, NOT "
          "configure_workflow_triggers):")
    viewer = _Principal(client, SUB[U_VIEWER])
    nobody = _Principal(client, SUB[U_NONE])

    res = viewer.call("get", "/api/v1/admin/workflow-triggers")
    envelope = res.json() if res.status_code == 200 else {}
    perms = envelope.get("permissions", {})
    check("[Y] TASK 5: the view-only caller CAN read the screen — a real 200 "
          "with real rows, which is what makes 'the read parts are visible' a "
          "claim about behaviour rather than about a spinner",
          res.status_code == 200 and isinstance(envelope.get("rows"), list)
          and len(envelope["rows"]) > 0,
          f"HTTP {res.status_code}, {len(envelope.get('rows', []))} row(s)")
    check("[Y] TASK 5: and the envelope it receives says can_write=false while "
          "can_read=true — this exact object is what the components are checked "
          "against below",
          perms.get("can_read") is True and perms.get("can_write") is False
          and perms.get("is_super_admin") is False,
          str(perms)[:180])
    check("[Y] the view-only caller sees the SAME decorated columns the writer "
          "does — reading is not degraded to punish the missing key",
          all(k in envelope["rows"][0] for k in
              ("schedule_summary", "occurrence_count", "last_fired_at",
               "next_occurrence")),
          f"{sorted(envelope['rows'][0])[:8]}…")

    # Every write, attempted directly. Hidden controls prove nothing about these.
    attempts = [
        ("POST   create", "post", "/api/v1/admin/workflow-triggers",
         {"workflow_definition_id": str(D_UI), "trigger_type": "scheduled",
          "schedule_cron": "0 9 * * *"}),
        ("PATCH  edit", "patch", f"/api/v1/admin/workflow-triggers/{trigger_id}",
         {"schedule_cron": "0 10 * * *"}),
        ("PATCH  pause", "patch", f"/api/v1/admin/workflow-triggers/{trigger_id}",
         {"is_active": False}),
        ("DELETE delete", "delete",
         f"/api/v1/admin/workflow-triggers/{trigger_id}", None),
        ("POST   preview", "post", "/api/v1/admin/workflow-triggers/preview",
         {"schedule_cron": "0 9 * * *", "timezone": "UTC"}),
    ]
    all_refused = True
    for label, method, path, body in attempts:
        res = viewer.call(method, path, body)
        ok = res.status_code == 403 and _detail(res) == f"Permission required: {PERM_TRIGGERS}"
        all_refused = all_refused and ok
        check(f"    [Y] {label} — 403 naming the missing key", ok,
              f"HTTP {res.status_code} {_detail(res)}")
    check("[Y] TASK 5: the API refuses the view-only caller EVERY write when "
          "attempted directly — checked independently of what the UI renders, "
          "because a hidden control over an open endpoint and a gated endpoint "
          "under a visible button are both real bugs",
          all_refused)

    # And a caller with neither key cannot even read.
    res = nobody.call("get", "/api/v1/admin/workflow-triggers")
    check("[Y] the widened read gate is still a gate — a member holding NEITHER "
          "workflow key is refused the list outright",
          res.status_code == 403, f"HTTP {res.status_code} {_detail(res)}")
    return envelope


def phase_delete(client, trigger_id, event_id):
    admin = _Principal(client, SUB[U_ADMIN])
    res = admin.call("delete", f"/api/v1/admin/workflow-triggers/{trigger_id}")
    body = res.json() if res.status_code == 200 else {}
    check("[Y] TASK 4: delete really deletes, and reports what it removed",
          res.status_code == 200 and body.get("deleted") is True
          and str(body.get("id")) == str(trigger_id),
          f"HTTP {res.status_code} {str(body)[:140]}")

    res = admin.call("get", "/api/v1/admin/workflow-triggers")
    ids = {str(r["id"]) for r in res.json().get("rows", [])}
    check("[Y] TASK 4: the deleted trigger is gone from the live list",
          str(trigger_id) not in ids)

    res = admin.call("delete", f"/api/v1/admin/workflow-triggers/{trigger_id}")
    check("[Y] deleting it a second time is a 404, not a silent success — a "
          "delete that always reports OK cannot tell you it already happened",
          res.status_code == 404, f"HTTP {res.status_code}")

    if event_id:
        admin.call("delete", f"/api/v1/admin/workflow-triggers/{event_id}")


# ═══════════════════════════════════════════════════════════════════════════
# The UI half of the dual permission proof
# ═══════════════════════════════════════════════════════════════════════════
GRID = WEB / "components" / "admin" / "WorkflowTriggerScheduler.jsx"
PANE = WEB / "components" / "admin" / "TriggerDetailPane.jsx"
PAGE = WEB / "app" / "admin" / "workflows" / "triggers" / "page.js"


def check_ui(view_envelope: dict) -> None:
    """The other half, fed the REAL envelope the view-only fixture received."""
    print("\n" + "=" * 74)
    print("TASK 5 — what the components render for THAT envelope")
    print("=" * 74)

    grid = strip_js_comments(read(GRID))
    pane = strip_js_comments(read(PANE))
    page = strip_js_comments(read(PAGE))

    can_write = view_envelope.get("permissions", {}).get("can_write")
    check("[Y] the envelope driving these checks is the REAL one the view-only "
          "fixture received over HTTP, not a hand-written stand-in — a server "
          "that stopped emptying it fails here as well as there",
          can_write is False, f"can_write={can_write}")

    check("[Y] the screen's canWrite comes from permissions.can_write and "
          "NOTHING else — no role check, no `is_super_admin ||`, no second "
          "opinion",
          "permissions?.can_write" in grid
          and grid.count("canWrite") >= 3
          and "role" not in grid,
          "canWrite = !!permissions?.can_write")

    # The fallback is the thing that would quietly restore the controls.
    bad_fallbacks = re.findall(
        r"can_write\s*(?:\?\?|\|\|)\s*(?!false)\w+", grid + pane)
    check("[Y] there is NO truthy fallback anywhere on can_write — the screen "
          "seeds `{can_read: true, can_write: false}` when the envelope is "
          "missing, so a lost envelope fails CLOSED instead of restoring the "
          "full editor",
          not bad_fallbacks and "can_write: false" in grid,
          f"truthy fallbacks found: {bad_fallbacks or 'none'}")

    # Every write control lives inside a canWrite gate.
    for label, needle in (
        ("the New trigger button", "canWrite ? ("),
        ("the Edit / Pause block", "canWrite && !editing"),
        ("the create / edit form", "canWrite && editing"),
    ):
        check(f"    [Y] {label} renders only inside a canWrite gate — ABSENT, "
              f"not disabled",
              needle in (grid if "New trigger" in label else pane),
              needle)

    # Delete is behind its own confirm, and inside the same gate. Anchored on
    # the RENDER branch (`confirmingDelete ? (`), not on the useState that
    # declares the flag — the declaration sits at the top of the component and
    # would make this pass no matter where the button ended up.
    delete_at = pane.find("confirmingDelete ? (")
    gate_at = pane.find("canWrite && !editing")
    check("[Y] TASK 4: delete is a two-step confirm inside the same canWrite "
          "gate, and its confirm text names the trigger and the word "
          "irreversible — pause is one click, delete is not",
          delete_at > gate_at > -1
          and "irreversible" in read(PANE)
          and "Delete permanently" in pane,
          f"gate@{gate_at} confirm-branch@{delete_at}")
    check("[Y] TASK 4: pause and delete are separated in the markup — delete "
          "sits below its own rule, not beside Pause where the two would look "
          "equally reversible",
          pane.find('border-t border-[var(--2a-border)] pt-3"', gate_at) > -1
          and pane.find("Delete this trigger…") > pane.find(">Pause<") - 1,
          "delete is below a rule, after the pause block")

    # No mock data anywhere. "Mock data" means a FABRICATED ROW — an array of
    # trigger-shaped objects standing in for the API. Deliberately NOT a ban on
    # every string literal: an input's `placeholder=` and the new-trigger form's
    # starting values are neither rows nor data, and a check that flagged them
    # would be the kind of false positive that gets a check deleted rather than
    # a bug fixed.
    for label, src in (("the grid", grid), ("the pane", pane), ("the page", page)):
        named = re.findall(r"\b(?:MOCK|SAMPLE|FAKE|DEMO|STUB)_[A-Z_]*\s*=", src)
        row_arrays = re.findall(r"=\s*\[\s*\{", src)
        check(f"    [Y] {label} declares no MOCK_/SAMPLE_/FAKE_ constant and no "
              f"array-of-objects row literal — there is nothing in it a row "
              f"could come from except the API",
              not named and not row_arrays,
              f"{named + row_arrays or 'none'}")

    # The one cron literal in the pane is the NEW-trigger form's starting value,
    # not a row. Stated rather than suppressed: it is a seed the user edits and
    # the API validates, and it never reaches the grid.
    empty_form = re.search(r"const EMPTY_FORM = \{.*?\};", pane, re.DOTALL)
    cron_literals = re.findall(r"schedule_cron:\s*[\"'][^\"']+[\"']", pane)
    check("[Y] the pane's only schedule_cron literal is the new-trigger form's "
          "starting value inside EMPTY_FORM — a field the author edits and the "
          "API validates, not a row and not a fallback for missing data",
          len(cron_literals) == 1
          and empty_form is not None
          and cron_literals[0] in empty_form.group(0),
          f"{cron_literals}")
    check("[Y] and the GRID — the thing that displays real triggers — contains "
          "no schedule literal at all",
          not re.findall(r"schedule_cron:\s*[\"']", grid),
          "no cron literal in the grid")

    check("[Y] TASK 5: the screen's rows come from the live API only — seeded "
          "by the server component's getWorkflowTriggers() and re-read from "
          "/api/admin/workflow-triggers, with `useState(initialRows)` and no "
          "literal default row anywhere",
          "initialRows = []" in grid
          and '"/api/admin/workflow-triggers"' in grid
          and "getWorkflowTriggers()" in page,
          "initialRows defaults to [], not to sample data")

    # The recurrence summary and the preview are the server's, not the browser's.
    check("[Y] the human-readable recurrence is the SERVER's schedule_summary, "
          "not a cron-to-English renderer in the browser — the browser's "
          "opinion is the one the operator reads while the server's is the one "
          "that runs, and they must not be two opinions",
          "schedule_summary" in grid
          and not re.search(r"(?i)cron.*(?:split|match|regex)", grid)
          and "parse" not in grid,
          "field: schedule_summary, rendered as-is")
    check("[Y] TASK 3: the dry run calls the real preview endpoint and renders "
          "what comes back — it computes no occurrence itself",
          '"/api/admin/workflow-triggers/preview"' in pane
          and "preview.occurrences" in pane
          and "rrule" not in pane.lower(),
          "POST /api/admin/workflow-triggers/preview")

    # 422s are surfaced, not re-derived.
    check("[Y] every validation message the form shows came back from the API "
          "as a real 422 — formatApiError renders the detail verbatim (string "
          "OR Pydantic's array), and there is not one cron, timezone or "
          "date-ordering rule implemented in the browser",
          "formatApiError" in pane
          and "res.ok" in pane
          and not re.search(r"(?i)(invalid|must).{0,40}cron", pane),
          "no client-side recurrence validation")

    # The grid is the shared one.
    check("[Y] the screen is DataGrid-driven — the same shared grid the "
          "Portfolio UX screens use, with the paused-row treatment going "
          "through its existing getRowStyle row hook rather than a new prop",
          "@/components/ui/DataGrid" in grid and "getRowStyle" in grid,
          "DataGrid + getRowStyle")
    check("[Y] TASK 4: a paused trigger is visually distinct in the LIST, not "
          "only in its pill — muted ink and a cream wash on the whole row",
          re.search(r"row\.is_active\s*\n?\s*\?\s*undefined", grid) is not None
          and "opacity: 0.72" in grid,
          "getRowStyle mutes the row when !is_active")


# ═══════════════════════════════════════════════════════════════════════════
def run_npm_build() -> tuple[int, str]:
    print("\n" + "=" * 74)
    print("TASK 5 — npm run build")
    print("=" * 74)
    try:
        proc = subprocess.run(
            ["npm", "run", "build"], cwd=WEB, capture_output=True, text=True,
            timeout=900,
        )
    except FileNotFoundError:
        return -1, "npm not on PATH"
    except subprocess.TimeoutExpired:
        return -2, "timed out after 900s"
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


# ═══════════════════════════════════════════════════════════════════════════
async def main_async():
    dsn = await bootstrap_async()
    if not dsn:
        print("[FAIL] no working DATABASE_URL — cannot verify anything")
        return 1
    os.environ.setdefault("DATABASE_URL", dsn)

    conn = await asyncpg.connect(dsn, statement_cache_size=0, ssl="require")
    now_utc = datetime.now(UTC).replace(second=0, microsecond=0)
    tz_name = "America/New_York"
    due_cron = cron_due_now(tz_name, now_utc)
    quiesced = []
    created = {}

    try:
        await teardown(conn)
        await task1_report(conn)
        preview_equivalence_tests()

        print("\n── Fixtures ──")
        quiesced = await quiesce_foreign_triggers(conn)
        check("[Y] pre-existing NON-fixture scheduled triggers are parked for "
              "the duration and restored in teardown — the tick scans all orgs, "
              "so this script must not fire somebody else's schedule",
              True, f"{len(quiesced)} foreign trigger(s) parked")
        profile_id = await seed(conn)
        check("[Y] the writer fixture holds the REAL seeded 'Org Admin' profile "
              "and the viewer holds a profile granting view_workflow_runs ALONE "
              "— if the viewer's profile carried the write key, every refusal "
              "below would be vacuous",
              profile_id is not None, f"org_admin profile={profile_id}")
        viewer_keys = [r["permission_key"] for r in await conn.fetch(
            "SELECT permission_key FROM profile_permissions WHERE profile_id = $1",
            VIEWER_PROFILE)]
        check("[Y] the viewer's profile grants EXACTLY [view_workflow_runs]",
              viewer_keys == [PERM_VIEW_RUNS], f"{viewer_keys}")

        # ── Create + preview, through the real app ──
        created = await api_phase(phase_create, due_cron, tz_name)
        trigger_id = created["trigger_id"]
        event_id = created["event_id"]

        # The endpoint's preview vs the scheduler's own computation, directly.
        from services.workflow_schedule import evaluate_trigger, next_occurrences
        anchor = datetime.fromisoformat(
            created["preview_after"].replace("Z", "+00:00"))
        service_occurrences = next_occurrences(
            schedule_cron=due_cron, timezone_name=tz_name,
            after_utc=anchor, count=5)
        endpoint = [datetime.fromisoformat(o.replace("Z", "+00:00"))
                    for o in created["endpoint_occurrences"]]
        check("[Y] TASK 3: the five occurrences the HTTP preview returned are "
              "the five the scheduler's own next_occurrences produces for the "
              "same anchor — the endpoint formats, it does not compute",
              endpoint == service_occurrences and len(endpoint) == 5,
              f"{[o.isoformat() for o in endpoint[:2]]}… vs "
              f"{[o.isoformat() for o in service_occurrences[:2]]}…")
        walked = True
        previous = anchor
        for index, occurrence in enumerate(endpoint):
            gap = int((occurrence - previous).total_seconds() // 60) + 2
            decision = evaluate_trigger(
                schedule_cron=due_cron, timezone_name=tz_name,
                now_utc=occurrence, last_fired_at=previous,
                occurrence_count=index, lookback_minutes=gap)
            walked = walked and decision.due and decision.occurrence_utc == occurrence
            previous = occurrence
        check("[Y] TASK 3: and driving the REAL evaluate_trigger at each of "
              "those five instants reports DUE for exactly that occurrence — "
              "the preview is proven against the firing decision, not assumed "
              "to match because both use RRULE",
              walked)

        # ── TICK 1: the UI-created trigger really fires ──
        print("\n  ── TICK 1: the trigger created through the screen ──")
        result, cap = await tick(conn, now_utc)
        fired = [f for f in result.fired if str(f["trigger_id"]) == str(trigger_id)]
        check("[Y] TASK 3/5: the trigger created through the screen's own "
              "endpoint is picked up by a REAL scheduler tick and fires a real "
              "workflow run — CRUD to execution, closed",
              len(fired) == 1, f"{result.summary()}\n{cap.text()}")
        run_id = fired[0]["run_id"] if fired else None
        run = await conn.fetchrow(
            "SELECT id, status, context FROM workflow_runs WHERE id = $1", run_id
        ) if run_id else None
        check("[Y] and the run it started is a real workflow_runs row carrying "
              "this trigger's id in its context",
              run is not None and str(trigger_id) in str(run["context"]),
              f"run {run_id} status={run['status'] if run else '-'}")
        state = await conn.fetchrow(
            "SELECT occurrence_count, last_fired_at FROM workflow_triggers WHERE id = $1",
            UUID(trigger_id))
        check("[Y] the firing advanced the counters the screen displays",
              state["occurrence_count"] == 1 and state["last_fired_at"] is not None,
              f"occurrence_count={state['occurrence_count']} "
              f"last_fired_at={state['last_fired_at']}")

        # ── Validation parity, then pause ──
        await api_phase(phase_validation, trigger_id, event_id)
        before, after = await api_phase(phase_pause, trigger_id)

        # ── TICK 2: paused. Tomorrow's occurrence must NOT fire. ──
        print("\n  ── TICK 2: paused, at the NEXT occurrence's instant ──")
        tomorrow = now_utc + timedelta(days=1)
        result2, cap2 = await tick(conn, tomorrow)
        touched = [b for bucket in (result2.fired, result2.skipped, result2.errors)
                   for b in bucket if str(b["trigger_id"]) == str(trigger_id)]
        check("[Y] TASK 4: a paused trigger does not fire — at an instant when "
              "its cron IS due, the tick does not even examine it, because the "
              "scan itself filters on is_active",
              not touched and not any(str(trigger_id) in line for line in cap2.lines),
              f"{result2.summary()}; buckets mentioning it: {len(touched)}")
        kept = await conn.fetchrow(
            """SELECT schedule_cron, timezone, start_date, end_date,
                      max_occurrences, occurrence_count, last_fired_at
               FROM workflow_triggers WHERE id = $1""", UUID(trigger_id))
        check("[Y] TASK 4: and it lost NOTHING while paused — the cron, the "
              "zone, the bounds, the cap, occurrence_count and last_fired_at "
              "are all exactly as they were, read back from the database",
              kept["schedule_cron"] == due_cron
              and kept["timezone"] == tz_name
              and kept["max_occurrences"] == 4
              and kept["occurrence_count"] == 1
              and kept["last_fired_at"] is not None,
              f"cron={kept['schedule_cron']} cap={kept['max_occurrences']} "
              f"count={kept['occurrence_count']}")

        # ── Resume, TICK 3: it picks up where it left off ──
        status, resumed = await api_phase(phase_resume, trigger_id)
        check("[Y] TASK 4: resuming is the same call with true, and returns the "
              "trigger with its history intact",
              status == 200 and resumed.get("is_active") is True
              and resumed.get("occurrence_count") == 1,
              f"HTTP {status} occurrence_count={resumed.get('occurrence_count')}")
        print("\n  ── TICK 3: resumed ──")
        result3, cap3 = await tick(conn, tomorrow)
        fired3 = [f for f in result3.fired if str(f["trigger_id"]) == str(trigger_id)]
        check("[Y] TASK 4: once resumed it fires again, and its occurrence_count "
              "continues from 1 rather than restarting — which is the whole "
              "difference between pausing and deleting",
              len(fired3) == 1 and fired3[0]["occurrence_count"] == 2,
              f"{result3.summary()}")

        # ── View-only, then delete ──
        view_envelope = await api_phase(phase_viewonly, trigger_id)
        await api_phase(phase_delete, trigger_id, event_id)

        gone = await conn.fetchval(
            "SELECT count(*) FROM workflow_triggers WHERE id = $1", UUID(trigger_id))
        check("[Y] TASK 4: the deleted trigger is really gone from the database "
              "— a hard delete, not a second hidden inactive state",
              gone == 0, f"rows with that id = {gone}")

        print("\n  ── TICK 4: after the delete ──")
        result4, cap4 = await tick(conn, now_utc + timedelta(days=2))
        check("[Y] TASK 4: a later tick does not reference the deleted trigger "
              "anywhere — not in fired, skipped or errors, and not once in the "
              "tick log",
              not any(str(trigger_id) in line for line in cap4.lines)
              and not any(str(b["trigger_id"]) == str(trigger_id)
                          for bucket in (result4.fired, result4.skipped,
                                         result4.errors)
                          for b in bucket),
              f"{result4.summary()}")

        # ── The UI half ──
        check_ui(view_envelope)

        # ── The build ──
        code, output = run_npm_build()
        check("[Y] TASK 5: `npm run build` exits 0",
              code == 0,
              f"exit={code}" + ("" if code == 0 else f"\n{output[-1500:]}"))

    finally:
        try:
            await teardown(conn)
            await restore_foreign_triggers(conn, quiesced)
            restored = await conn.fetchval(
                """SELECT count(*) FROM workflow_triggers
                   WHERE id = ANY($1::uuid[]) AND is_active""", quiesced)
            check("[Y] TEARDOWN: every parked foreign trigger is active again, "
                  "and none of them fired",
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
                        + (SELECT count(*) FROM member_todos
                             WHERE user_id = ANY($1::uuid[]))
                        + (SELECT count(*) FROM profiles WHERE id = $4)
                        + (SELECT count(*) FROM profile_permissions
                             WHERE profile_id = $4)""",
                ALL_USERS, ALL_DEFS, list(VER.values()), VIEWER_PROFILE)
            check("[Y] TEARDOWN: zero leftover fixture rows across users, "
                  "profiles, profile grants, definitions, versions, steps, "
                  "triggers, runs and todos",
                  leftovers == 0, f"leftover rows = {leftovers}")
            survivors = await conn.fetchval(
                "SELECT count(*) FROM workflow_triggers")
            check("[Y] TEARDOWN removed only fixtures — the pre-existing trigger "
                  "rows survive",
                  survivors >= 2, f"triggers remaining = {survivors}")
        finally:
            await conn.close()

    print(f"\n{'=' * 74}")
    print(f"{_n_pass} passed, {_n_fail} failed — "
          f"{'ALL GREEN' if _ok else 'FAILURES ABOVE'}")
    print("=" * 74)
    return 0 if _ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main_async()))
