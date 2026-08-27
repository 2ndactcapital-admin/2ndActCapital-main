"""verify_schedulernotify.py — scheduler notifications: what was missing, and what was built.

This sprint was framed as mostly CONFIRMATION, and that framing held. The
held-run alert path (``workflow_engine._hold_run`` →
``workflow_todos.create_held_run_alerts`` → ``member_todos``) was already
proven, already correct for scheduler-fired runs, and is NOT re-litigated or
duplicated here. What this script proves is the three discovery answers and the
two small, real additions the third one justified.

WHAT THIS PROVES (against the DEPLOYED database and the REAL firing loop —
no stubs, no mocked scheduler, no hand-written todo rows where a real one
would do):

  [Task 1a] There is NO warning of any kind for a schedule that keeps SKIPPING.
            Still true of the code as it stands, and measured that narrowly:
            the scheduler is PARSED and only the branches that record a skip
            are searched for a durable write, the tick's only caller is checked
            for persistence, and the database is asked whether any column could
            hold a consecutive-skip count. A skip lands in a returned dataclass
            and a log line and nowhere else. Reported as a real gap — and
            deliberately NOT built, with the reason stated.
  [Task 1b] The two orphaned ``workflow_run_held`` todos are NOT "silently
            inert" in the way the prompt supposed, and not harmless either.
            Proven both directions against the live rows: they DO come back
            from the dashboard's own query, and they CANNOT be reached from
            the run console, because the run they name does not exist.
  [Task 1c] Nothing anywhere warned that a schedule is about to stop. Proven by
            searching the pre-sprint tree for any such marker — the tree LOCATED
            by pre_sprint_ref(), not HEAD, because Task 3 built precisely this
            warning and HEAD would hand back the sprint's own answer as evidence
            the gap never existed. Confirmed gap.
  [Task 2]  The REAL live orphans are resolved by a REAL tick, and a NEW orphan
            manufactured the way the real ones were made — a held run's alert,
            then the run deleted — is cleaned up by the next tick. A held-run
            alert whose run still exists is proven untouched.
  [Task 3]  A trigger one occurrence from its cap raises the warning on its
            second-to-last REAL tick; a trigger with four occurrences left,
            fired by the SAME tick, does not. The end_date bound is proven the
            same way, and an unbounded trigger is proven never to warn.
  [Task 4]  Cross-org isolation on BOTH new paths: the expiring alert reaches
            only the trigger's own org, and the org-scoped sweep leaves the
            other org's orphan alone.
  [Teardown] Zero leftover fixture rows — including the alert todos raised on
            REAL org admins, which is exactly the cleanup whose absence
            created the orphans this sprint fixes.

Run:  apps/api/venv/bin/python apps/api/scripts/verify_schedulernotify.py
"""
import ast
import asyncio
import pathlib
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from uuid import UUID

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from _db_bootstrap import bootstrap_async  # noqa: E402  (also puts apps/api on sys.path)

import asyncpg  # noqa: E402

UTC = timezone.utc
REPO = pathlib.Path(__file__).resolve().parents[3]

ORG_ID = UUID("00000000-0000-0000-0000-000000000001")        # 2nd Act Capital
OTHER_ORG_ID = UUID("bb347258-8f28-4f49-8cc9-e29ccad82884")  # Hollisworks

# Fixture users. The two org_admins matter: the recipient rule for both alert
# kinds is "the person who configured it, plus every org_admin of that org", so
# a cross-org claim is only meaningful with an admin on each side.
U_AUTHOR = UUID("99000000-0000-0000-0000-00000000b1a1")   # org1, configures triggers
U_ADMIN = UUID("99000000-0000-0000-0000-00000000b1a2")    # org1, org_admin
U_OTHER_AUTHOR = UUID("99000000-0000-0000-0000-00000000b1a3")  # org2
U_OTHER_ADMIN = UUID("99000000-0000-0000-0000-00000000b1a4")   # org2, org_admin
ALL_USERS = [U_AUTHOR, U_ADMIN, U_OTHER_AUTHOR, U_OTHER_ADMIN]
SUB = {
    U_AUTHOR: "schednotify_author",
    U_ADMIN: "schednotify_admin",
    U_OTHER_AUTHOR: "schednotify_other_author",
    U_OTHER_ADMIN: "schednotify_other_admin",
}
ROLE = {
    U_AUTHOR: "member",
    U_ADMIN: "org_admin",
    U_OTHER_AUTHOR: "member",
    U_OTHER_ADMIN: "org_admin",
}
ORG_OF = {
    U_AUTHOR: ORG_ID, U_ADMIN: ORG_ID,
    U_OTHER_AUTHOR: OTHER_ORG_ID, U_OTHER_ADMIN: OTHER_ORG_ID,
}

# One definition per behaviour: the overlap guard is per (definition, org), so
# sharing a definition between two triggers would make the second one skip and
# quietly turn a firing assertion into a vacuous one.
D_CAP = UUID("99000000-0000-0000-0000-00000000b1c1")    # one occurrence from its cap
D_MANY = UUID("99000000-0000-0000-0000-00000000b1c2")   # four occurrences left
D_END = UUID("99000000-0000-0000-0000-00000000b1c3")    # bounded by end_date
D_FREE = UUID("99000000-0000-0000-0000-00000000b1c4")   # unbounded
D_OTHER = UUID("99000000-0000-0000-0000-00000000b1c5")  # org2
D_ORPHAN = UUID("99000000-0000-0000-0000-00000000b1c6")      # org1 orphan scenario
D_ORPHAN2 = UUID("99000000-0000-0000-0000-00000000b1c7")     # org2 orphan scenario
ALL_DEFS = [D_CAP, D_MANY, D_END, D_FREE, D_OTHER, D_ORPHAN, D_ORPHAN2]
VER = {d: UUID(str(d).replace("b1c", "b1d", 1)) for d in ALL_DEFS}

T_CAP = UUID("99000000-0000-0000-0000-00000000b1e1")
T_MANY = UUID("99000000-0000-0000-0000-00000000b1e2")
T_END = UUID("99000000-0000-0000-0000-00000000b1e3")
T_FREE = UUID("99000000-0000-0000-0000-00000000b1e4")
T_OTHER = UUID("99000000-0000-0000-0000-00000000b1e5")
ALL_TRIGGERS = [T_CAP, T_MANY, T_END, T_FREE, T_OTHER]

BPMN_NS = "http://www.omg.org/spec/BPMN/20100524/MODEL"

# Stamped into every fixture held run's error_detail, and therefore into the
# alert todos raised from it. THIS IS THE TEARDOWN HANDLE, and the first run of
# this script proved why one is needed: the orphan scenario deliberately
# deletes its runs, so a teardown that derives todo ids from surviving runs can
# no longer find those alerts — and the copies raised on REAL org admins are
# not covered by the fixture-user list either. That is the exact bug this
# sprint fixes, and the first version of this teardown committed it.
FIXTURE_MARKER = "schedulernotify orphan scenario"

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
    """Collects the tick's log lines, so "logged visibly" can be asserted."""

    def __init__(self, echo=True):
        self.lines = []
        self.echo = echo

    def __call__(self, message):
        self.lines.append(str(message))
        if self.echo:
            print(f"        │ {message}")

    def text(self):
        return "\n".join(self.lines)


def git_text(args: list[str]) -> str:
    """``git <args>`` in this repo, stdout only, empty string on any failure."""
    out = subprocess.run(
        ["git", "-C", str(REPO), *args], capture_output=True, text=True, timeout=60,
    )
    return out.stdout if out.returncode == 0 else ""


def git_show(rel_path: str, ref: str = "HEAD") -> str:
    """The file as ``ref`` holds it; empty string if it did not exist there."""
    return git_text(["show", f"{ref}:{rel_path}"])


# A name this sprint INTRODUCED. The commit that first added it is therefore
# this sprint's commit, and its parent is the tree Task 1 is asking about.
_SPRINT_MARKER = "dismiss_orphaned_run_alerts"
_SPRINT_MARKER_PATH = "apps/api/services/workflow_todos.py"


def pre_sprint_ref() -> str:
    """The tree as it stood BEFORE this sprint's own additions.

    Task 1 asks what was MISSING, so it needs a reference point that this
    sprint's answers are not already part of. The first version of this script
    used ``HEAD``, which was right exactly once — while the sprint was still
    uncommitted. The moment it landed, HEAD became the tree WITH the orphan
    sweep and the expiring-schedule warning in it, and three discovery checks
    began reading this sprint's own work back as proof that the gap never
    existed: 1a saw the sweep's ``import workflow_todos``, 1b saw the sweep
    itself, 1c saw the expiring alert.

    So the reference point is LOCATED, not assumed and not hard-coded to a sha:
    ``git log -S`` names the commit that first introduced the sweep, and its
    parent is the pre-sprint tree. Before that commit exists the marker is not
    in history at all and HEAD is once again the correct answer, so this reads
    the same tree whether the sprint is committed or not.
    """
    shas = git_text(["log", "--format=%H", "-S", _SPRINT_MARKER,
                     "--", _SPRINT_MARKER_PATH]).split()
    return f"{shas[-1]}^" if shas else "HEAD"


def skip_branch_writers(rel_path: str) -> tuple[list[str], list[str]]:
    """(durable writes inside a SKIP branch, every todo writer in the file).

    The narrow question 1a asks is whether a SKIP ever becomes durable. "Does
    workflow_scheduler.py call a todo writer at all" is a different question,
    and since Tasks 2 and 3 it answers *yes* for two reasons that have nothing
    to do with skipping — which is exactly how the first version of this check
    went wrong. So the file is parsed instead of grepped: every branch that
    appends to ``TickResult.skipped`` is found, and only that branch is searched
    for a call that would outlive the tick.

    The second list is printed, not asserted on, so a reader can see WHY the
    file writes todos and judge for themselves that neither reason is a skip.
    """
    tree = ast.parse((REPO / rel_path).read_text())

    def records_a_skip(node) -> bool:
        return any(
            isinstance(n, ast.Attribute) and n.attr == "skipped"
            and isinstance(n.value, ast.Name) and n.value.id == "result"
            for n in ast.walk(node)
        )

    def durable_calls(node) -> list[str]:
        out = []
        for n in ast.walk(node):
            if not (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)):
                continue
            owner = getattr(n.func.value, "id", "")
            if owner == "workflow_todos" or n.func.attr in (
                "execute", "executemany", "fetch", "fetchrow", "fetchval",
            ):
                out.append(f"{owner}.{n.func.attr}" if owner else n.func.attr)
        return out

    in_skip_branch = []
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and records_a_skip(node):
            in_skip_branch += durable_calls(node)

    todo_writers = sorted({
        f"workflow_todos.{n.func.attr}" for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        and getattr(n.func.value, "id", "") == "workflow_todos"
    })
    return sorted(set(in_skip_branch)), todo_writers


def trivial_bpmn(proc_id) -> str:
    """Start -> End. Runs to 'completed', so it never blocks the next tick."""
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


# ── fixtures ────────────────────────────────────────────────────────────────
async def _mk_user(conn, uid):
    sub = SUB[uid]
    await conn.execute(
        """INSERT INTO users (id, org_id, email, full_name, auth0_sub, role, is_active)
           VALUES ($1, $2, $3, $4, $5, $6, true)
           ON CONFLICT (auth0_sub) DO UPDATE
             SET role = EXCLUDED.role, org_id = EXCLUDED.org_id, is_active = true""",
        uid, ORG_OF[uid], f"{sub}@test.local", sub, sub, ROLE[uid],
    )


async def _mk_definition(conn, def_id, org_id, name, created_by):
    await conn.execute(
        """INSERT INTO workflow_definitions (id, org_id, name, description, created_by)
           VALUES ($1, $2, $3, 'schedulernotify fixture', $4)
           ON CONFLICT (id) DO NOTHING""",
        def_id, org_id, name, created_by,
    )
    await conn.execute(
        """INSERT INTO workflow_versions
             (id, workflow_definition_id, org_id, version_number, bpmn_xml,
              change_summary, is_current, created_by)
           VALUES ($1, $2, $3, 1, $4, 'v1', true, $5)
           ON CONFLICT (id) DO NOTHING""",
        VER[def_id], def_id, org_id,
        trivial_bpmn(f"schednotify_{str(def_id)[-4:]}"), created_by,
    )


async def _mk_trigger(conn, *, trigger_id, def_id, org_id, cron, created_by,
                      end_date=None, max_occurrences=None, occurrence_count=0):
    await conn.execute(
        """INSERT INTO workflow_triggers
             (id, workflow_definition_id, org_id, trigger_type, schedule_cron,
              timezone, end_date, max_occurrences, occurrence_count, is_active,
              created_by)
           VALUES ($1, $2, $3, 'scheduled', $4, 'UTC', $5, $6, $7, true, $8)
           ON CONFLICT (id) DO UPDATE
             SET schedule_cron = EXCLUDED.schedule_cron,
                 end_date = EXCLUDED.end_date,
                 max_occurrences = EXCLUDED.max_occurrences,
                 occurrence_count = EXCLUDED.occurrence_count,
                 last_fired_at = NULL, is_active = true""",
        trigger_id, def_id, org_id, cron, end_date, max_occurrences,
        occurrence_count, created_by,
    )


async def _mk_held_run_with_alert(conn, *, def_id, org_id, started_by, detail):
    """A REAL held run and a REAL alert, through create_held_run_alerts itself.

    Hand-writing the member_todos row would prove the sweep can find a row this
    script wrote. Calling the actual writer proves it can find the row the
    engine writes.
    """
    from services import workflow_todos

    run_id = await conn.fetchval(
        """INSERT INTO workflow_runs
             (workflow_version_id, org_id, status, started_by, error_detail)
           VALUES ($1, $2, 'held', $3, $4) RETURNING id""",
        VER[def_id], org_id, started_by, detail,
    )
    ids = await workflow_todos.create_held_run_alerts(
        conn, org_id=org_id, run_id=run_id, started_by=started_by,
        error_detail=detail,
    )
    return run_id, ids


def cron_at(moment: datetime) -> str:
    """A daily UTC cron matching ``moment``'s minute.

    UTC on purpose. The timezone machinery is proven by verify_schedulercore;
    what THIS script needs is a cron that is due at an injected instant and due
    again exactly 24h later, which a DST-free zone guarantees.
    """
    m = moment.astimezone(UTC)
    return f"{m.minute} {m.hour} * * *"


async def quiesce_foreign_triggers(conn):
    """Park every non-fixture scheduled trigger, returning what to restore.

    The tick scans all orgs by design. The deployed database holds a real
    scheduled trigger ('0 9 * * *', UTC), and this script drives ticks at
    injected instants including one 24 hours ahead — so without this it would
    fire somebody else's workflow for real.
    """
    rows = await conn.fetch(
        """SELECT id FROM workflow_triggers
           WHERE trigger_type = 'scheduled' AND is_active
             AND id <> ALL($1::uuid[])""",
        ALL_TRIGGERS)
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


async def teardown(conn):
    """FK-safe, and — the point of this whole sprint — scoped by LINKAGE as well
    as by fixture user.

    Both alert kinds fan out to every real ``org_admin`` in the org. A teardown
    that deleted only ``user_id = ANY(fixture users)`` would strand the real
    admins' copies pointing at deleted rows, which is precisely how the two
    live orphans this sprint cleans up came to exist.
    """
    run_ids = [r["id"] for r in await conn.fetch(
        """SELECT r.id FROM workflow_runs r
           JOIN workflow_versions v ON v.id = r.workflow_version_id
           WHERE v.workflow_definition_id = ANY($1::uuid[])""", ALL_DEFS)]
    await conn.execute(
        """DELETE FROM member_todos
           WHERE (related_type = 'workflow_trigger' AND related_id = ANY($1::uuid[]))
              OR (related_type = 'workflow_run' AND related_id = ANY($2::uuid[]))
              OR user_id = ANY($3::uuid[])
              OR (source = 'workflow_run_held' AND detail LIKE '%' || $4 || '%')""",
        ALL_TRIGGERS, run_ids, ALL_USERS, FIXTURE_MARKER)
    await conn.execute(
        "DELETE FROM workflow_triggers WHERE id = ANY($1::uuid[])", ALL_TRIGGERS)
    await conn.execute(
        "DELETE FROM workflow_triggers WHERE workflow_definition_id = ANY($1::uuid[])",
        ALL_DEFS)
    await conn.execute(
        "DELETE FROM workflow_run_steps WHERE workflow_run_id = ANY($1::uuid[])", run_ids)
    await conn.execute("DELETE FROM workflow_runs WHERE id = ANY($1::uuid[])", run_ids)
    await conn.execute(
        "DELETE FROM workflow_runs WHERE started_by = ANY($1::uuid[])", ALL_USERS)
    await conn.execute(
        "DELETE FROM workflow_steps WHERE workflow_version_id = ANY($1::uuid[])",
        list(VER.values()))
    await conn.execute(
        "DELETE FROM workflow_versions WHERE workflow_definition_id = ANY($1::uuid[])",
        ALL_DEFS)
    await conn.execute(
        "DELETE FROM workflow_definitions WHERE id = ANY($1::uuid[])", ALL_DEFS)
    await conn.execute("DELETE FROM audit_log WHERE user_id = ANY($1::uuid[])", ALL_USERS)
    await conn.execute("DELETE FROM users WHERE id = ANY($1::uuid[])", ALL_USERS)


# ═══════════════════════════════════════════════════════════════════════════
# TASK 1 — the three findings, measured now
# ═══════════════════════════════════════════════════════════════════════════
async def task1_report(conn):
    from services import workflow_todos

    pre_ref = pre_sprint_ref()
    pre_sha = git_text(["rev-parse", "--short", pre_ref]).strip() or pre_ref

    print("\n" + "=" * 74)
    print("TASK 1 — DISCOVERY (measured live, not quoted)")
    print("=" * 74)
    print(f"\n  The tree Task 1 measures against, for the questions that are "
          f"about\n  what was MISSING: {pre_ref} ({pre_sha}) — the commit "
          f"BEFORE this sprint's own\n  sweep and expiring-schedule warning "
          f"were added. See pre_sprint_ref().")

    pre_todos = git_show("apps/api/services/workflow_todos.py", pre_ref)

    # ── 1a — a WARNING for a schedule that keeps SKIPPING ───────────────────
    # Asked of the code AS IT STANDS TODAY, on purpose: this gap was not closed
    # this sprint, so the finding is present-tense and needs no historical tree.
    print("\n  1a. Is there ANY alert for a scheduled trigger that keeps skipping?")
    print("      Where a skip goes, in the code as it stands right now:")
    print("        * evaluate_trigger returns ScheduleDecision(due=False, reason=…)")
    print("        * the tick logs it and appends to TickResult.skipped")
    print("        * the OVERLAP skip is logged loudly as 'SKIP-OVERLAP'")
    print("        * workflow_scheduler_tick.py prints the summary and exits")
    # NARROW BY CONSTRUCTION. Not "does this file write todos" — it does, twice,
    # and neither is about a skip. The three things that would have to be true
    # for a consecutive-skip alert to exist are asked one at a time:
    #   (1) a skip branch writing something that outlives the tick,
    #   (2) the only process that calls the tick persisting the result,
    #   (3) a column anywhere that could hold a consecutive-skip count.
    in_skip_branch, todo_writers = skip_branch_writers(
        "apps/api/services/workflow_scheduler.py")
    tick_src = (REPO / "apps/api/workflow_scheduler_tick.py").read_text()
    persists = any(w in tick_src for w in ("INSERT", "member_todos", "notification"))
    skip_columns = [f"{r['table_name']}.{r['column_name']}" for r in await conn.fetch(
        """SELECT table_name, column_name FROM information_schema.columns
           WHERE table_schema = 'public'
             AND (column_name ILIKE '%skip%' OR column_name ILIKE '%consecutive%')
           ORDER BY table_name, column_name""")]
    todos_src = (REPO / "apps/api/services/workflow_todos.py").read_text()
    skip_sources = [ln.split("=")[0].strip() for ln in todos_src.splitlines()
                    if ln.startswith("TODO_SOURCE") and "skip" in ln.lower()]
    print(f"        durable writes inside a branch that records a SKIP        "
          f": {in_skip_branch or 'none'}")
    print(f"        (for contrast, every todo writer the scheduler calls at all"
          f": {todo_writers} — the Task 2 sweep and the Task 3 expiry notice, "
          f"neither reached from a skip)")
    print(f"        workflow_scheduler_tick.py persists the tick result anywhere"
          f"  : {persists}")
    print(f"        member_todos source markers about skipping                "
          f": {skip_sources or 'none'}")
    print(f"        columns in the database that could hold a skip count      "
          f": {skip_columns or 'none'}")
    check("[Y] TASK 1a — FINDING, reported honestly: NO warning-level alert "
          "exists for a schedule that is consistently SKIPPING. A skip is "
          "visible ONLY in the cron process's stdout and in a dataclass that "
          "nothing stores. This IS a real gap",
          not in_skip_branch and not persists and not skip_sources
          and not skip_columns,
          "skips reach a log line and an in-memory TickResult; nothing durable, "
          "and no column exists that a skip count could go in")
    print("\n      NOT BUILT THIS SPRINT, and the reason is not 'no time':")
    print("      'consistently skipping' is a statement about a RUN of ticks, and")
    print("      nothing persists tick outcomes — there is no column or table")
    print("      holding a consecutive-skip count, and adding one is a schema")
    print("      change this sprint has no Part 1 SQL for. Task 3 asked for the")
    print("      end-of-life warning specifically; inventing a second alert on a")
    print("      state the database cannot yet express would be the manufactured")
    print("      work the prompt told us not to do. Recorded as an open gap.")

    # ── 1b — the two live orphans ──────────────────────────────────────────
    # Status is NOT filtered here on purpose. This sprint's fix is permanent and
    # idempotent, so the very first run of this script moves the real orphans
    # from 'open' to 'dismissed' — and a discovery pass that only counted open
    # rows would then report "no orphans found" on every later run and quietly
    # stop proving anything. Both states are read, and reported for what they
    # are.
    orphans = await conn.fetch(
        """SELECT t.id, t.org_id, t.user_id, t.status, t.detail, t.related_id,
                  t.created_at, u.email, u.role
           FROM member_todos t
           LEFT JOIN users u ON u.id = t.user_id
           WHERE t.source = $1 AND t.related_type = 'workflow_run'
             AND t.related_id IS NOT NULL
             AND NOT EXISTS (SELECT 1 FROM workflow_runs r WHERE r.id = t.related_id)
           ORDER BY t.created_at""",
        workflow_todos.TODO_SOURCE_RUN_HELD)
    open_orphans = [o for o in orphans if o["status"] == "open"]
    swept_orphans = [o for o in orphans
                     if o["status"] == "dismissed"
                     and "no longer exists" in (o["detail"] or "")]

    print(f"\n  1b. Held-run alert todos whose run is GONE, live right now: "
          f"{len(orphans)}  "
          f"({len(open_orphans)} still open, {len(swept_orphans)} already "
          f"closed by this sprint's sweep)")
    for o in orphans:
        print(f"        todo {str(o['id'])[:8]}… status={o['status']:<9} "
              f"→ run {str(o['related_id'])[:8]}… (gone)  "
              f"recipient={o['email']} role={o['role']}  "
              f"created {o['created_at'].isoformat()}")

    fixture_recipients = [o for o in orphans
                          if str(o["user_id"]).startswith("99000000")]
    check("[Y] TASK 1b — the orphans belong to REAL org admins, not to "
          "leftover fixture users: create_held_run_alerts fans out to every "
          "users.role='org_admin' in the org, so a teardown scoped to its own "
          "fixture user ids strands the real admins' copies",
          len(orphans) > 0 and len(fixture_recipients) == 0,
          f"{len(orphans)} orphan(s), {len(fixture_recipients)} of them on "
          f"fixture users")

    # Are they visible? Asked with the dashboard's OWN filter, not a paraphrase.
    visible = 0
    for o in orphans:
        visible += int(await conn.fetchval(
            """SELECT count(*) FROM member_todos
               WHERE user_id = $1 AND org_id = $2 AND status = 'open' AND id = $3""",
            o["user_id"], o["org_id"], o["id"]) or 0)
    print(f"\n      Reaching their recipient's dashboard right now "
          f"(routers/dashboard.py list_todos' filter: user + org + "
          f"status='open'): {visible}/{len(orphans)}")
    if open_orphans:
        check("[Y] TASK 1b — FINDING, and it corrects the prompt's premise: an "
              "orphan is NOT silently inert. Every open one comes back from "
              "the dashboard todo list and the 'needs attention' brief block, "
              "both of which filter on status='open' alone",
              visible == len(open_orphans),
              f"{visible} of {len(open_orphans)} open orphans are live on "
              f"somebody's dashboard")
    else:
        check("[Y] TASK 1b — SAME FINDING, seen from the other side: these "
              "orphans WERE reaching a real admin's dashboard (they are "
              "status='open' rows, which is the only thing list_todos and the "
              "brief block filter on) and this sprint's sweep has since closed "
              "every one, so none reaches it now",
              visible == 0 and len(swept_orphans) == len(orphans),
              f"{visible} still on a dashboard; {len(swept_orphans)} of "
              f"{len(orphans)} closed by the sweep, each carrying its reason")

    reachable = 0
    for o in orphans:
        reachable += int(await conn.fetchval(
            "SELECT count(*) FROM workflow_runs WHERE id = $1", o["related_id"]) or 0)
    router_404 = 'raise HTTPException(status_code=404, detail="Run not found")' in \
        git_show("apps/api/routers/workflows.py")
    check("[Y] TASK 1b — but they are UNRESOLVABLE IN CONTEXT: the alert's "
          "action_key points at the run console, GET /admin/workflow-runs/{id} "
          "404s when the run is missing, so the pane that would explain the "
          "alert can never render. Half-visible, wholly unactionable",
          reachable == 0 and router_404,
          f"{reachable} of {len(orphans)} orphaned runs still exist; "
          f"router 404s on a missing run = {router_404}")

    # ORPHAN HANDLING SPECIFICALLY, and read at the pre-sprint ref. The sweep
    # this sprint wrote lives in this very file, so asking HEAD "does
    # workflow_todos.py close orphaned alerts" answers with this sprint's own
    # work. Three parts, all of which a sweep must have: it must name
    # workflow_runs, it must key on that row's ABSENCE, and it must close the
    # todo rather than merely read it.
    names_runs = "workflow_runs" in pre_todos
    anti_join = "NOT EXISTS" in pre_todos
    closes = "dismiss" in pre_todos.lower()
    had_cleanup = names_runs and anti_join and closes
    print(f"\n      workflow_todos.py at {pre_sha} — the three things an orphan "
          f"sweep needs:")
    print(f"        names workflow_runs: {names_runs}   keys on its absence "
          f"(NOT EXISTS): {anti_join}   closes the todo: {closes}")
    check("[Y] TASK 1b — and NOTHING closed them: the pre-sprint "
          "workflow_todos.py has no orphan handling at all, so a stranded "
          "alert stays open forever unless its recipient dismisses it by hand",
          not had_cleanup,
          f"workflow_todos.py at {pre_sha} has no sweep")

    # The real, recurring cause — named, not guessed.
    deleters = []
    for p in sorted((REPO / "apps/api/scripts").glob("verify_*.py")):
        text = p.read_text(errors="ignore")
        if "DELETE FROM workflow_runs" in text and "member_todos" not in text:
            deleters.append(p.name)
    app_deleters = subprocess.run(
        ["git", "-C", str(REPO), "grep", "-l", "DELETE FROM workflow_runs",
         "--", "apps/api/routers", "apps/api/services"],
        capture_output=True, text=True, timeout=60).stdout.split()
    print("\n      THE REAL CAUSE, before choosing a fix:")
    print(f"        production code paths that DELETE a workflow_runs row : "
          f"{len(app_deleters)} {app_deleters}")
    print(f"        verify teardowns that delete runs and NEVER touch "
          f"member_todos: {deleters}")
    check("[Y] TASK 1b — the cause is real and recurring, and it is NOT a "
          "production deleter: no router or service deletes a workflow_runs "
          "row at all. Every deleter in the repo is a test teardown, and "
          "several clean up no todos whatsoever — so a one-time data migration "
          "would be stale again the next time one of them runs",
          len(app_deleters) == 0 and len(deleters) > 0,
          f"{len(app_deleters)} production deleters, {len(deleters)} unguarded "
          f"teardowns")

    # ── 1c — the end-of-life warning ───────────────────────────────────────
    # Product code only, at the PRE-SPRINT ref. Two scopings, both load-bearing:
    #   * the ref, because Task 3 built exactly this warning — searching HEAD
    #     finds the sprint's own answer and calls the gap imaginary;
    #   * the paths, because apps/api/scripts holds an unrelated verify script
    #     that says "expiring" about an access token, and counting it would
    #     turn a real measurement into a word match.
    tree = git_text(
        ["grep", "-il", "expiring\\|occurrences remaining\\|will stop running",
         pre_ref, "--", "apps/api/services", "apps/api/routers", "apps/web/app",
         "apps/web/components"]).split()
    print(f"\n  1c. Anything warning a schedule is about to stop, before this "
          f"sprint?\n      matches at {pre_sha}: {tree or 'none'}")
    print("      What that tree does with the bounds instead:")
    print("        evaluate_trigger returns 'max_occurrences reached (3/3)' and")
    print("        'past end_date …' — reasons for NOT firing, produced only")
    print("        once the schedule has ALREADY stopped, and delivered to a log.")
    check("[Y] TASK 1c — FINDING: there is NO forward-looking warning. A "
          "bounded schedule simply goes quiet; the first sign is a report that "
          "did not arrive. The only mention of the bounds is a skip reason "
          "emitted after the last run has already happened",
          not tree,
          f"no code at {pre_sha} mentions an expiring or ending schedule")

    return {
        "open": [o["id"] for o in open_orphans],
        "swept": [o["id"] for o in swept_orphans],
    }


# ═══════════════════════════════════════════════════════════════════════════
# TASK 2 — the orphan sweep, against the live database
# ═══════════════════════════════════════════════════════════════════════════
async def orphan_tests(conn, real_orphans):
    from services import workflow_todos

    print("\n" + "=" * 74)
    print("TASK 2 — the orphaned-alert sweep")
    print("=" * 74)

    # A REAL held run + REAL alert in each org, plus one in org1 whose run is
    # left ALIVE. The sweep must tell these three apart.
    orphan_run_1, ids_1 = await _mk_held_run_with_alert(
        conn, def_id=D_ORPHAN, org_id=ORG_ID, started_by=U_AUTHOR,
        detail=f"RuntimeError: {FIXTURE_MARKER}, org 1")
    orphan_run_2, ids_2 = await _mk_held_run_with_alert(
        conn, def_id=D_ORPHAN2, org_id=OTHER_ORG_ID, started_by=U_OTHER_AUTHOR,
        detail=f"RuntimeError: {FIXTURE_MARKER}, org 2")
    living_run, ids_live = await _mk_held_run_with_alert(
        conn, def_id=D_ORPHAN, org_id=ORG_ID, started_by=U_AUTHOR,
        detail=f"RuntimeError: {FIXTURE_MARKER}, LIVING held run")

    check("[Y] the fixture alerts were written by the REAL "
          "create_held_run_alerts, and reached the org's real admins too — "
          "which is what makes the orphan scenario the real one",
          len(ids_1) >= 2 and len(ids_2) >= 1 and len(ids_live) >= 2,
          f"org1 orphan={len(ids_1)} recipients, org2 orphan={len(ids_2)}, "
          f"living={len(ids_live)}")

    # Now delete the two runs the way a teardown does — and ONLY the runs.
    await conn.execute("DELETE FROM workflow_runs WHERE id = ANY($1::uuid[])",
                       [orphan_run_1, orphan_run_2])
    still_open = await conn.fetchval(
        """SELECT count(*) FROM member_todos
           WHERE source = $1 AND related_id = ANY($2::uuid[]) AND status = 'open'""",
        workflow_todos.TODO_SOURCE_RUN_HELD, [orphan_run_1, orphan_run_2])
    check("[Y] deleting the runs really does strand their alerts — the todos "
          "survive the run's deletion, open and pointing at nothing "
          "(member_todos.related_id is polymorphic and carries no FK)",
          still_open == len(ids_1) + len(ids_2),
          f"{still_open} stranded alert todo(s)")

    # ── the org-scoped sweep: cross-org isolation, both directions ──────────
    # Scoped to org 2 ON PURPOSE. The pre-existing REAL orphans live in org 1,
    # and sweeping org 1 here would close them before the tick ever ran — the
    # tick's "the real orphans are resolved" claim would then be true of a
    # direct call this script made, not of the scheduler. Everything org 1 is
    # left for the real tick to find.
    swept = await workflow_todos.dismiss_orphaned_run_alerts(
        conn, org_id=OTHER_ORG_ID)
    o1 = await conn.fetch(
        "SELECT status FROM member_todos WHERE source = $1 AND related_id = $2",
        workflow_todos.TODO_SOURCE_RUN_HELD, orphan_run_1)
    o2 = await conn.fetch(
        "SELECT status, detail FROM member_todos WHERE source = $1 AND related_id = $2",
        workflow_todos.TODO_SOURCE_RUN_HELD, orphan_run_2)
    live = await conn.fetch(
        "SELECT status FROM member_todos WHERE source = $1 AND related_id = $2",
        workflow_todos.TODO_SOURCE_RUN_HELD, living_run)
    real_still_open = await conn.fetchval(
        """SELECT count(*) FROM member_todos
           WHERE id = ANY($1::uuid[]) AND status = 'open'""", real_orphans["open"])
    real_still_swept = await conn.fetchval(
        """SELECT count(*) FROM member_todos
           WHERE id = ANY($1::uuid[]) AND status = 'dismissed'""",
        real_orphans["swept"])

    check("[Y] TASK 2: the org-scoped sweep dismissed org 2's orphaned alerts, "
          "and dismissed EXACTLY those — the returned count matches the rows "
          "that actually changed",
          len(o2) > 0 and all(r["status"] == "dismissed" for r in o2)
          and swept == len(o2),
          f"swept={swept}, statuses={[r['status'] for r in o2]}")
    check("[Y] CROSS-ORG: and left org 1's orphan strictly alone — same "
          "condition, different tenant, untouched",
          len(o1) > 0 and all(r["status"] == "open" for r in o1),
          f"org1 statuses={[r['status'] for r in o1]}")
    if real_orphans["open"]:
        check("[Y] CROSS-ORG: including the REAL pre-existing orphans, which "
              "are org 1's — so the tick below is genuinely what resolves them",
              real_still_open == len(real_orphans["open"]),
              f"{real_still_open} of {len(real_orphans['open'])} still open")
    else:
        check("[Y] CROSS-ORG: and the REAL orphans, org 1's, are untouched by "
              "an org 2 sweep — they were already closed by an earlier real "
              "tick of this same code and stay closed",
              real_still_swept == len(real_orphans["swept"])
              and len(real_orphans["swept"]) > 0,
              f"{real_still_swept} of {len(real_orphans['swept'])} still "
              f"dismissed")
    check("[Y] and the alert whose run STILL EXISTS is untouched — the sweep "
          "keys on the run's absence, not on the source marker",
          len(live) > 0 and all(r["status"] == "open" for r in live),
          f"living-run alert statuses={[r['status'] for r in live]}")
    check("[Y] a dismissed orphan says WHY it was dismissed, and is dismissed "
          "rather than deleted — the record that a run really held stays "
          "auditable, it just stops demanding action",
          all("no longer exists" in (r["detail"] or "") for r in o2),
          (o2[0]["detail"] or "")[-90:] if o2 else "")

    return {"orphan_run_1": orphan_run_1, "orphan_run_2": orphan_run_2,
            "living_run": living_run, "real_orphans": real_orphans,
            "n_org1_orphan_alerts": len(ids_1)}


# ═══════════════════════════════════════════════════════════════════════════
# TASK 3 — the expiring-schedule warning, driven by REAL ticks
# ═══════════════════════════════════════════════════════════════════════════
async def expiry_tests(conn, pool, now_utc, orphan_state):
    from services import workflow_todos
    from services.workflow_scheduler import run_scheduler_tick

    print("\n" + "=" * 74)
    print("TASK 3 — the expiring-schedule warning, and TASK 2's live proof")
    print("=" * 74)

    cron = cron_at(now_utc)
    tomorrow = now_utc + timedelta(days=1)

    # T_CAP  : cap 3, already at 1 → this tick makes it 2, ONE run left  → WARN
    # T_MANY : cap 5, already at 0 → this tick makes it 1, FOUR left     → silent
    # T_END  : no cap; end_date 25h out → exactly one occurrence left    → WARN
    # T_FREE : no bounds at all                                          → silent
    # T_OTHER: org 2, cap 2 at 1 → this tick makes it 2, NONE left       → WARN
    await _mk_trigger(conn, trigger_id=T_CAP, def_id=D_CAP, org_id=ORG_ID,
                      cron=cron, created_by=U_AUTHOR,
                      max_occurrences=3, occurrence_count=1)
    await _mk_trigger(conn, trigger_id=T_MANY, def_id=D_MANY, org_id=ORG_ID,
                      cron=cron, created_by=U_AUTHOR,
                      max_occurrences=5, occurrence_count=0)
    await _mk_trigger(conn, trigger_id=T_END, def_id=D_END, org_id=ORG_ID,
                      cron=cron, created_by=U_AUTHOR,
                      end_date=now_utc + timedelta(hours=25))
    await _mk_trigger(conn, trigger_id=T_FREE, def_id=D_FREE, org_id=ORG_ID,
                      cron=cron, created_by=U_AUTHOR)
    await _mk_trigger(conn, trigger_id=T_OTHER, def_id=D_OTHER, org_id=OTHER_ORG_ID,
                      cron=cron, created_by=U_OTHER_AUTHOR,
                      max_occurrences=2, occurrence_count=1)

    print(f"\n  Fixture cron {cron!r} (UTC) is due at the injected instant "
          f"{now_utc.isoformat()},")
    print(f"  and again 24h later at {tomorrow.isoformat()}. Both ticks below "
          f"are REAL:")
    print("  the same run_scheduler_tick apps/api/workflow_scheduler_tick.py calls.")

    async def alerts_for(trigger_id):
        return await conn.fetch(
            """SELECT t.id, t.org_id, t.user_id, t.title, t.detail, t.status,
                      t.priority, t.kind, t.category, t.action_key, t.related_type,
                      u.role, u.email
               FROM member_todos t LEFT JOIN users u ON u.id = t.user_id
               WHERE t.source = $1 AND t.related_type = 'workflow_trigger'
                 AND t.related_id = $2
               ORDER BY u.email""",
            workflow_todos.TODO_SOURCE_TRIGGER_EXPIRING, trigger_id)

    # ── TICK 1 ──────────────────────────────────────────────────────────────
    print("\n  ── TICK 1 (real) ──")
    cap1 = Capture()
    r1 = await run_scheduler_tick(conn, pool, now_utc=now_utc, log=cap1)
    fired1 = {f["trigger_id"] for f in r1.fired}

    check("[Y] tick 1 fired all five fixture triggers for real",
          all(t in fired1 for t in ALL_TRIGGERS),
          f"{r1.summary()}")

    # ── TASK 2's live proof, on the real tick ───────────────────────────────
    real_orphans = orphan_state["real_orphans"]
    live_ids = real_orphans["open"] or real_orphans["swept"]
    live_now = await conn.fetch(
        """SELECT id, status, detail FROM member_todos
           WHERE id = ANY($1::uuid[])""", live_ids)
    org1_orphan = await conn.fetch(
        "SELECT status FROM member_todos WHERE source = $1 AND related_id = $2",
        workflow_todos.TODO_SOURCE_RUN_HELD, orphan_state["orphan_run_1"])
    living = await conn.fetch(
        "SELECT status FROM member_todos WHERE source = $1 AND related_id = $2",
        workflow_todos.TODO_SOURCE_RUN_HELD, orphan_state["living_run"])

    resolved_by = ("this tick" if real_orphans["open"]
                   else "an earlier real tick of this same code")
    check("[Y] TASK 2 — THE REAL ORPHANS ARE RESOLVED: the "
          f"{len(live_ids)} orphaned held-run alert(s) that were live in the "
          "deployed database before this sprint are now 'dismissed' with the "
          f"sweep's own reason recorded on them — closed by {resolved_by}, "
          "against the real database",
          len(live_now) == len(live_ids) and len(live_ids) > 0
          and all(r["status"] == "dismissed" for r in live_now)
          and all("no longer exists" in (r["detail"] or "") for r in live_now),
          f"{[r['status'] for r in live_now]}")
    check("[Y] TASK 2 — and the NEW orphan, made the way the real ones were "
          "(a real held run's alert, then the run deleted), is cleaned up by "
          "the same tick — it sat in the org the earlier org-scoped sweep had "
          "correctly refused to touch",
          len(org1_orphan) > 0 and all(r["status"] == "dismissed" for r in org1_orphan),
          f"org1 fixture orphan now {[r['status'] for r in org1_orphan]}")
    check("[Y] TASK 2 — the platform sweep still leaves the alert whose run "
          "exists open, so 'dismiss orphans' never became 'dismiss held-run "
          "alerts'",
          len(living) > 0 and all(r["status"] == "open" for r in living),
          f"living-run alert {[r['status'] for r in living]}")
    expected_sweep = (len(real_orphans["open"])
                      + orphan_state["n_org1_orphan_alerts"])
    check("[Y] TASK 2 — and the sweep is VISIBLE and EXACT: the tick logs what "
          "it closed and reports precisely the rows it changed — every open "
          "orphan this tick could see and nothing else. Rows an earlier tick "
          "already closed are not re-counted, so the sweep is idempotent",
          r1.orphaned_alerts_dismissed == expected_sweep and "SWEEP" in cap1.text(),
          f"orphaned_alerts_dismissed={r1.orphaned_alerts_dismissed}, "
          f"expected {expected_sweep}")

    # ── TASK 3: fires at the right tick, and not before ─────────────────────
    a_cap = await alerts_for(T_CAP)
    a_many = await alerts_for(T_MANY)
    a_end = await alerts_for(T_END)
    a_free = await alerts_for(T_FREE)

    cap_count = await conn.fetchval(
        "SELECT occurrence_count FROM workflow_triggers WHERE id = $1", T_CAP)
    many_count = await conn.fetchval(
        "SELECT occurrence_count FROM workflow_triggers WHERE id = $1", T_MANY)

    check("[Y] TASK 3: a trigger ONE occurrence from its cap raised the warning "
          "on its second-to-last REAL tick — cap 3, occurrence_count now 2, so "
          "the next fire is the last one",
          cap_count == 2 and len(a_cap) > 0
          and all(r["title"] == "Schedule ending — one run left" for r in a_cap),
          f"occurrence_count={cap_count}, {len(a_cap)} alert(s): "
          f"{a_cap[0]['title'] if a_cap else '-'}")
    check("[Y] TASK 3: and a trigger with FOUR occurrences left, fired by the "
          "SAME tick, raised nothing — the warning is about the cap being "
          "near, not about the cap existing",
          many_count == 1 and len(a_many) == 0,
          f"occurrence_count={many_count}, alerts={len(a_many)}")
    check("[Y] TASK 3: the end_date bound warns the same way — one occurrence "
          "left before the end date, one warning, and the notice names the "
          "date rather than saying only 'it will stop'",
          len(a_end) > 0 and all("end date" in (r["detail"] or "") for r in a_end),
          (a_end[0]["detail"] if a_end else "")[:150])
    check("[Y] TASK 3: an UNBOUNDED trigger never warns — no cap, no end date, "
          "nothing to be near the end of. (This is the shape of the real "
          "scheduled trigger already deployed, which must stay quiet)",
          len(a_free) == 0, f"alerts={len(a_free)}")
    check("[Y] TASK 3: the cap warning explains WHICH bound ends it, in the "
          "operator's own numbers",
          len(a_cap) > 0 and "limit of 3 runs" in (a_cap[0]["detail"] or ""),
          (a_cap[0]["detail"] if a_cap else "")[:170])

    # ── the alert's shape, asserted against the writer's own constants ──────
    shape = a_cap[0] if a_cap else None
    check("[Y] TASK 3: the warning is a member_todos row on the SAME mechanism "
          "as the held-run alert — a distinct source marker, related to the "
          "TRIGGER, category 'workflow', open, actionable from the trigger "
          "console. Not a second notification system",
          shape is not None
          and shape["related_type"] == "workflow_trigger"
          and shape["status"] == "open"
          and shape["kind"] == "actual"
          and shape["category"] == workflow_todos.TODO_CATEGORY
          and shape["action_key"] == "/admin/workflows/triggers"
          and workflow_todos.TODO_SOURCE_TRIGGER_EXPIRING == "workflow_trigger_expiring",
          f"source={workflow_todos.TODO_SOURCE_TRIGGER_EXPIRING} "
          f"related_type={shape['related_type'] if shape else '-'} "
          f"action_key={shape['action_key'] if shape else '-'}")

    recips_cap = {r["user_id"] for r in a_cap}
    org1_admins = {r["id"] for r in await conn.fetch(
        "SELECT id FROM users WHERE org_id = $1 AND role = 'org_admin'", ORG_ID)}
    check("[Y] TASK 3: recipients are the trigger's author PLUS every "
          "org_admin of that org — the same rule create_held_run_alerts uses, "
          "reused rather than re-invented",
          recips_cap == ({U_AUTHOR} | org1_admins),
          f"{len(recips_cap)} recipient(s), org admins matched="
          f"{org1_admins <= recips_cap}, author included={U_AUTHOR in recips_cap}")

    # ── CROSS-ORG on the new alert path ────────────────────────────────────
    a_other = await alerts_for(T_OTHER)
    other_orgs = {r["org_id"] for r in a_other}
    other_recips = {r["user_id"] for r in a_other}
    org2_admins = {r["id"] for r in await conn.fetch(
        "SELECT id FROM users WHERE org_id = $1 AND role = 'org_admin'", OTHER_ORG_ID)}
    check("[Y] CROSS-ORG: org 2's expiring trigger alerted org 2 ONLY — every "
          "row carries org 2's org_id and every recipient is an org 2 user",
          len(a_other) > 0 and other_orgs == {OTHER_ORG_ID}
          and other_recips == ({U_OTHER_AUTHOR} | org2_admins)
          and not (other_recips & org1_admins),
          f"{len(a_other)} alert(s), orgs={[str(o)[:8] for o in other_orgs]}, "
          f"leaked to org1 admins={bool(other_recips & org1_admins)}")
    check("[Y] CROSS-ORG, the other direction: org 1's expiring trigger did "
          "NOT reach org 2 — an org_admin is an admin OF AN ORG, not of the "
          "platform",
          not (recips_cap & ({U_OTHER_AUTHOR} | org2_admins)),
          f"org2 users receiving org1's alert = "
          f"{len(recips_cap & ({U_OTHER_AUTHOR} | org2_admins))}")
    check("[Y] CROSS-ORG: and org 2's trigger, whose cap this tick EXHAUSTED, "
          "says so — the final-run notice, not the one-left notice",
          len(a_other) > 0
          and all(r["title"] == "Schedule ended — no further runs" for r in a_other),
          a_other[0]["title"] if a_other else "-")

    # ── TICK 2, 24h later: the last fire ───────────────────────────────────
    print("\n  ── TICK 2 (real, +24h) ──")
    cap2 = Capture()
    r2 = await run_scheduler_tick(conn, pool, now_utc=tomorrow, log=cap2)
    fired2 = {f["trigger_id"] for f in r2.fired}

    cap_ids_before = {r["id"] for r in a_cap}
    a_cap2 = await alerts_for(T_CAP)
    a_many2 = await alerts_for(T_MANY)
    cap_count2 = await conn.fetchval(
        "SELECT occurrence_count FROM workflow_triggers WHERE id = $1", T_CAP)

    check("[Y] TASK 3: on the LAST tick the notice becomes 'ended' — same "
          "trigger, one occurrence later, cap now spent",
          T_CAP in fired2 and cap_count2 == 3 and len(a_cap2) > 0
          and all(r["title"] == "Schedule ended — no further runs" for r in a_cap2),
          f"occurrence_count={cap_count2}, "
          f"title={a_cap2[0]['title'] if a_cap2 else '-'}")
    check("[Y] TASK 3: and it UPDATED the same todo rows rather than stacking a "
          "second alert on every recipient — the upsert keys on "
          "(user, org, source, related_type, related_id), so an operator gets "
          "one live notice per schedule, not one per tick",
          {r["id"] for r in a_cap2} == cap_ids_before
          and len(a_cap2) == len(a_cap),
          f"{len(a_cap)} → {len(a_cap2)} alert(s), ids identical="
          f"{ {r['id'] for r in a_cap2} == cap_ids_before }")
    check("[Y] TASK 3: the four-remaining trigger fired a second real time and "
          "is STILL silent — three left is still not near the end",
          T_MANY in fired2 and len(a_many2) == 0,
          f"alerts={len(a_many2)} after a second fire")
    check("[Y] TASK 3: tick 2's expiring alerts are reported on the result and "
          "in the log, like every other outcome the tick produces",
          len(r2.expiring) >= 1 and "EXPIRING" in cap2.text(),
          f"{r2.summary()}")

    # ── the alert is a *dashboard* alert, not a private table ───────────────
    on_dashboard = await conn.fetchval(
        """SELECT count(*) FROM member_todos
           WHERE user_id = $1 AND org_id = $2 AND kind = 'actual' AND status = 'open'
             AND source = $3""",
        U_ADMIN, ORG_ID, workflow_todos.TODO_SOURCE_TRIGGER_EXPIRING)
    check("[Y] TASK 3: the warning really lands where an operator will see it "
          "— it satisfies routers/dashboard.py list_todos' filter and "
          "brief_blocks' 'needs attention' filter for a real org_admin, with "
          "no new endpoint and no new surface",
          on_dashboard >= 1, f"{on_dashboard} open alert(s) for the org admin")


# ═══════════════════════════════════════════════════════════════════════════
async def main_async():
    global _ok
    print("=" * 74)
    print("verify_schedulernotify.py — scheduler notifications")
    print("=" * 74)

    url = await bootstrap_async()
    if not url:
        print("[FAIL] no working DATABASE_URL")
        return 1
    conn = await asyncpg.connect(url, statement_cache_size=0, ssl="require")

    now_utc = datetime.now(UTC).replace(second=0, microsecond=0)
    quiesced = []
    orphan_state = None
    try:
        await teardown(conn)
        quiesced = await quiesce_foreign_triggers(conn)
        print(f"\n[setup] parked {len(quiesced)} foreign scheduled trigger(s) "
              f"for the duration")

        for uid in ALL_USERS:
            await _mk_user(conn, uid)
        for def_id, org, name, owner in (
            (D_CAP, ORG_ID, "SCHEDNOTIFY Cap", U_AUTHOR),
            (D_MANY, ORG_ID, "SCHEDNOTIFY Many", U_AUTHOR),
            (D_END, ORG_ID, "SCHEDNOTIFY EndDate", U_AUTHOR),
            (D_FREE, ORG_ID, "SCHEDNOTIFY Unbounded", U_AUTHOR),
            (D_OTHER, OTHER_ORG_ID, "SCHEDNOTIFY OtherOrg", U_OTHER_AUTHOR),
            (D_ORPHAN, ORG_ID, "SCHEDNOTIFY Orphan", U_AUTHOR),
            (D_ORPHAN2, OTHER_ORG_ID, "SCHEDNOTIFY Orphan2", U_OTHER_AUTHOR),
        ):
            await _mk_definition(conn, def_id, org, name, owner)

        real_orphans = await task1_report(conn)
        orphan_state = await orphan_tests(conn, real_orphans)

        from services.database import close_pool, get_pool

        pool = await get_pool()
        try:
            await expiry_tests(conn, pool, now_utc, orphan_state)
        finally:
            await close_pool()
    finally:
        try:
            # The live orphans stay dismissed — that IS the fix. Confirm the
            # sweep dismissed them rather than deleting them, then confirm
            # teardown did not resurrect or remove them.
            if orphan_state:
                real_ids = (orphan_state["real_orphans"]["open"]
                            or orphan_state["real_orphans"]["swept"])
                survivors = await conn.fetch(
                    "SELECT id, status FROM member_todos WHERE id = ANY($1::uuid[])",
                    real_ids)
                await teardown(conn)
                after = await conn.fetch(
                    "SELECT id, status FROM member_todos WHERE id = ANY($1::uuid[])",
                    real_ids)
                check("[Y] TEARDOWN: the real pre-existing orphans are still "
                      "THERE and still 'dismissed' — the fix closed them, it "
                      "did not delete somebody's history, and this script did "
                      "not undo its own repair on the way out",
                      len(survivors) == len(after) == len(real_ids)
                      and len(real_ids) > 0
                      and all(r["status"] == "dismissed" for r in after),
                      f"{len(after)} row(s), statuses={[r['status'] for r in after]}")
            else:
                await teardown(conn)

            await restore_foreign_triggers(conn, quiesced)
            restored = await conn.fetchval(
                """SELECT count(*) FROM workflow_triggers
                   WHERE id = ANY($1::uuid[]) AND is_active""", quiesced)
            check("[Y] TEARDOWN: every parked foreign trigger is active again",
                  restored == len(quiesced), f"{restored}/{len(quiesced)}")

            leftovers = await conn.fetchval(
                """SELECT (SELECT count(*) FROM users WHERE id = ANY($1::uuid[]))
                        + (SELECT count(*) FROM workflow_definitions
                             WHERE id = ANY($2::uuid[]) OR created_by = ANY($1::uuid[]))
                        + (SELECT count(*) FROM workflow_versions
                             WHERE workflow_definition_id = ANY($2::uuid[]))
                        + (SELECT count(*) FROM workflow_triggers
                             WHERE id = ANY($3::uuid[])
                                OR workflow_definition_id = ANY($2::uuid[])
                                OR created_by = ANY($1::uuid[]))
                        + (SELECT count(*) FROM workflow_runs
                             WHERE started_by = ANY($1::uuid[])
                                OR workflow_version_id = ANY($4::uuid[]))
                        + (SELECT count(*) FROM member_todos
                             WHERE user_id = ANY($1::uuid[])
                                OR (related_type = 'workflow_trigger'
                                    AND related_id = ANY($3::uuid[]))
                                OR detail LIKE '%' || $5 || '%')""",
                ALL_USERS, ALL_DEFS, ALL_TRIGGERS, list(VER.values()),
                FIXTURE_MARKER)
            check("[Y] TEARDOWN: zero leftover rows — including every alert "
                  "todo raised on a REAL org admin by a fixture trigger, "
                  "deleted by LINKAGE and not merely by fixture user id",
                  leftovers == 0, f"leftover rows = {leftovers}")

            new_orphans = await conn.fetchval(
                """SELECT count(*) FROM member_todos t
                   WHERE t.source = 'workflow_run_held'
                     AND t.related_type = 'workflow_run'
                     AND t.related_id IS NOT NULL AND t.status = 'open'
                     AND NOT EXISTS (
                       SELECT 1 FROM workflow_runs r WHERE r.id = t.related_id)""")
            check("[Y] TEARDOWN: and this script left NO new open orphan behind "
                  "— the class of bug it fixes is not one it commits",
                  new_orphans == 0, f"open orphans remaining = {new_orphans}")
        finally:
            await conn.close()

    print(f"\n{'=' * 74}")
    print(f"{_n_pass} passed, {_n_fail} failed — "
          f"{'ALL GREEN' if _ok else 'FAILURES ABOVE'}")
    print("=" * 74)
    return 0 if _ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main_async()))
