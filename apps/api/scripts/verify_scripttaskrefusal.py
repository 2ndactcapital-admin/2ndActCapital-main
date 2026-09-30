"""verify_scripttaskrefusal.py — Script Tasks are refused outright, never run.

THE GAP (confirmed live by wave2discovery.lowrisk, see
docs/WORKFLOW_WAVE2_DISCOVERY.md Task 3): SpiffWorkflow's stock
``PythonScriptEngine`` is in use with NO subclass. A ``bpmn:scriptTask``
therefore runs arbitrary Python IN PROCESS, with the application's own
database credentials, outside the action registry, outside every permission
check, outside audit, and outside the custody cliff. The NL generator never
offered ``scriptTask`` to the model, but the hand-edit diagram-editor path had
no such restriction — an org admin could drag one from bpmn-js's stock
palette, write arbitrary Python into it, save it (validation did not
object), and the next run executed it.

THE FIX (already committed, this script proves it, does not build it):
  * ``services.workflow_steps_deriver.find_script_tasks`` — a raw lxml scan
    (QName localname, any namespace prefix) for any ``scriptTask`` element.
  * ``services.workflow_nl_generator._validate`` — the ONE shared validator
    both writers of ``workflow_versions.bpmn_xml`` call — refuses any BPMN
    containing a Script Task BEFORE attempting a SpiffWorkflow parse, naming
    every offending element.
  * ``services.workflow_engine._ScriptTaskParser`` — registered in
    ``_make_bpmn_parser``'s ``OVERRIDE_PARSER_CLASSES`` dict, the SAME
    mechanism already used for ``_BusinessRuleTaskParser`` — defense in depth
    at the SpiffWorkflow parse layer itself for any future caller of
    ``parse_bpmn`` that bypasses ``_validate``.
  * ``WorkflowDiagramEditor.jsx`` — a best-effort, non-enforcing client-side
    guard (undoes a Script Task the instant it lands on the canvas). Usability
    only; this script's Task 3 assertion (a hand-crafted XML POST straight to
    the API) is the one that proves the REAL control is server-side.

TASK 1 FINDINGS (reported live, not assumed):
  1a. Live query of workflow_versions.bpmn_xml across ALL orgs for any
      scriptTask element. As of this sprint, three workflow_versions rows
      exist in total (WFMGR4 Definition v1/v2 in org
      00000000-0000-0000-0000-000000000001, WFMGR4 OtherOrg v1 in org
      bb347258-8f28-4f49-8cc9-e29ccad82884) — test fixtures, not real
      business workflows — and none contain a scriptTask: this sprint is
      PURELY PREVENTIVE, not remedial. This script re-runs the query fresh
      against whatever is live NOW (never hardcodes that count) and reports
      the real answer, including definition names + org ids for every row
      scanned. If the live answer is ever non-empty, that is an INCIDENT
      finding, not a code finding, and is reported loudly, not silently
      fixed here.
  1b. Confirms ONE shared validator, not two independently-fixed ones:
      ``workflow_editor.save_new_version`` calls
      ``workflow_nl_generator.validate_workflow_bpmn``, which — like the
      generator's own ``_generate_once`` — calls the same private
      ``_validate``. Proven by source inspection, not assumed from either
      call site's docstring.
  1c. Confirms ``_ScriptTaskParser`` is registered via the SAME
      ``OVERRIDE_PARSER_CLASSES`` mechanism as ``_BusinessRuleTaskParser``
      (same dict, same ``full_tag()`` helper, same ``TaskParser`` base) —
      not a second, unrelated approach.
  1d. Reports whether the bpmn-js palette can be trimmed client-side (it can,
      for usability) and states plainly that this can never be the real
      control — Task 3 below proves the server refuses regardless of what
      the client sends.

WHAT THIS PROVES:
  [Y] Task 1's four findings, reported live.
  [Y] Hand-edit save (``workflow_editor.save_new_version`` called directly,
      the exact function the API route delegates to) refuses a Script Task,
      naming the element, and the target definition's current version is
      BYTE-FOR-BYTE unchanged afterwards (re-read from an independent query).
  [Y] The generator path (``workflow_nl_generator.generate_workflow``, AI
      call hermetically faked — same seam as verify_workflowmgr2.py) refuses
      the same shape of BPMN independently — not assumed from 1b's shared
      code path, actually exercised end to end including the retry loop.
  [Y] A hand-crafted XML POST straight to the REAL ASGI app
      (``POST /api/v1/admin/workflows/{id}/versions`` via TestClient,
      bypassing any client-side palette restriction entirely) is still
      refused with a 422 naming the element — the assertion that proves
      enforcement is server-side.
  [Y] A legitimate BPMN with Service, User, Send and Gateway elements still
      saves (validated, persisted, steps correctly derived for all four
      types) and a SEPARATE legitimate run-only BPMN drives to a genuine
      ``completed`` status via ``start_workflow_run`` — no regression.
  [Y] An existing stored definition (a businessRuleTask + serviceTask BPMN
      written directly to the database, simulating a pre-sprint row, never
      touched by either writer) still parses via ``parse_bpmn`` and still
      runs to completion via ``start_workflow_run`` unchanged — proves the
      new ``OVERRIDE_PARSER_CLASSES`` entry didn't disturb the existing one.
  [Y] Cross-org: a SECOND, independent fixture org is refused identically on
      the same Script Task BPMN, and a legitimate save in that same org still
      works — proving org B isn't just broadly broken.
  [Y] Teardown: zero leftover rows. audit_log / assistant_activities /
      agent_proposals cleared BEFORE fixture users are deleted (both are
      children of users) — the exact ordering that broke a prior sprint's
      teardown on its second run.

Hydrates DATABASE_URL from Doppler over HTTPS at startup (the
verify_selfapproval.py / verify_actionregistryfix.py pattern). Never prints a
credential value. RLS context is set on every connection that touches an
RLS-protected table — ``SELECT set_config('app.is_super_admin', 'true',
true)`` inside an explicit transaction for raw fixture/query connections
(CLAUDE.md's ``platform_scope()`` convention), and
``services.database.set_rls_context`` / ``reset_rls_context`` around every
call into the real service layer through the real RLS-wrapped pool.

Pass/fail only. Prints a final ``TOTAL: N PASS, M FAIL`` line and exits
non-zero on any failure.

Run:  python3 apps/api/scripts/verify_scripttaskrefusal.py
"""
from __future__ import annotations

import asyncio
import inspect
import pathlib
import sys
import types
from uuid import UUID

HERE = pathlib.Path(__file__).resolve()
sys.path.insert(0, str(HERE.parent))
from _db_bootstrap import bootstrap_async  # noqa: E402  (also puts apps/api on sys.path)

import asyncpg  # noqa: E402

from SpiffWorkflow.bpmn.parser.util import full_tag  # noqa: E402

from services.assistant_actions import register_all  # noqa: E402
import services.workflow_editor as editor  # noqa: E402
import services.workflow_engine as eng  # noqa: E402
import services.workflow_nl_generator as nlgen  # noqa: E402
from services.workflow_editor import WorkflowValidationError, save_new_version  # noqa: E402
from services.workflow_engine import parse_bpmn, start_workflow_run  # noqa: E402
from services.workflow_nl_generator import WorkflowGenerationError, generate_workflow  # noqa: E402
from services.workflow_steps_deriver import derive_and_store_steps, derive_steps, find_script_tasks  # noqa: E402

# ═══════════════════════════════════════════════════════════════════════════
# Constants / fixtures
# ═══════════════════════════════════════════════════════════════════════════
ORG_A = UUID("00000000-0000-0000-0000-000000000001")            # 2nd Act — real org
ORG_B = UUID("99000000-0000-0000-0000-0000fb0b0001")             # fixture second org — cross-org proof

READ_ACTION = "marketplace.show_new_deals"   # access_type = read, real registered verb

# Fixture users — SUB derived from the FULL uuid hex, never a prefix slice
# (two fixture UUIDs sharing their first 8 characters collided on
# users_email_key two sprints ago).
U_CREATOR_A = UUID("99000000-0000-0000-0000-0000fc0a0001")
U_API_A = UUID("99000000-0000-0000-0000-0000fc0a0002")
U_CREATOR_B = UUID("99000000-0000-0000-0000-0000fc0b0001")
ALL_FIXTURE_USERS = [U_CREATOR_A, U_API_A, U_CREATOR_B]
ALL_FIXTURE_ORGS = [ORG_B]


def _sub(uid: UUID) -> str:
    return f"scripttaskrefusal_{uid.hex}"


# Definitions / versions.
DEF_HANDEDIT = UUID("99000000-0000-0000-0000-0000fd0a0001")
VER_HANDEDIT = UUID("99000000-0000-0000-0000-0000fd0a0002")
DEF_API = UUID("99000000-0000-0000-0000-0000fd0a0003")
VER_API = UUID("99000000-0000-0000-0000-0000fd0a0004")
DEF_LEGIT = UUID("99000000-0000-0000-0000-0000fd0a0005")
VER_LEGIT = UUID("99000000-0000-0000-0000-0000fd0a0006")
DEF_RUNNABLE = UUID("99000000-0000-0000-0000-0000fd0a0007")
VER_RUNNABLE = UUID("99000000-0000-0000-0000-0000fd0a0008")
DEF_LEGACY = UUID("99000000-0000-0000-0000-0000fd0a0009")
VER_LEGACY = UUID("99000000-0000-0000-0000-0000fd0a000a")
DEF_ORGB = UUID("99000000-0000-0000-0000-0000fd0b0001")
VER_ORGB = UUID("99000000-0000-0000-0000-0000fd0b0002")
ALL_DEF_IDS = [DEF_HANDEDIT, DEF_API, DEF_LEGIT, DEF_RUNNABLE, DEF_LEGACY, DEF_ORGB]

NAME_MARKER = "SCRIPTTASKREFUSAL_TEST"

ORG_ADMIN_PROFILE = "Org Admin"
FIXTURE_PROFILE_B = "SCRIPTTASKREFUSAL Verify Org B Author"

HEADERS = {"Authorization": "Bearer verify-token"}

BPMN_NS = "http://www.omg.org/spec/BPMN/20100524/MODEL"

_ok = True
_n_pass = 0
_n_fail = 0
_finds: list[str] = []


def check(passed: bool, label: str, detail: str = "") -> bool:
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
# Hermetic fake `anthropic` — same seam as verify_workflowmgr2.py / verify_s27.
# services/extraction.py imports `anthropic` LAZILY inside the functions that
# build a client, so swapping sys.modules["anthropic"] before calling
# generate_workflow intercepts it regardless of which transport branch runs.
# ═══════════════════════════════════════════════════════════════════════════
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
    async def create(self, *, model=None, max_tokens=None, system=None, messages=None, tools=None, **_):
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


# ═══════════════════════════════════════════════════════════════════════════
# BPMN builders
# ═══════════════════════════════════════════════════════════════════════════
def _script_bpmn(suffix: str, script_id: str = "Evil_1") -> str:
    """A minimal process whose only actionable element is a Script Task."""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<bpmn:definitions xmlns:bpmn="{BPMN_NS}" id="D_{suffix}" '
        'targetNamespace="http://2ndactcapital.com/bpmn">'
        f'<bpmn:process id="proc_{suffix}" isExecutable="true">'
        '<bpmn:startEvent id="Start_1"><bpmn:outgoing>f1</bpmn:outgoing></bpmn:startEvent>'
        f'<bpmn:scriptTask id="{script_id}" name="Evil script" scriptFormat="Python">'
        '<bpmn:incoming>f1</bpmn:incoming><bpmn:outgoing>f2</bpmn:outgoing>'
        '<bpmn:script>import os; os.system("id")</bpmn:script>'
        '</bpmn:scriptTask>'
        '<bpmn:endEvent id="End_1"><bpmn:incoming>f2</bpmn:incoming></bpmn:endEvent>'
        f'<bpmn:sequenceFlow id="f1" sourceRef="Start_1" targetRef="{script_id}"/>'
        f'<bpmn:sequenceFlow id="f2" sourceRef="{script_id}" targetRef="End_1"/>'
        '</bpmn:process></bpmn:definitions>'
    )


def _script_bpmn_with_valid_refs(suffix: str, action_key: str, profile_id, script_id: str = "Evil_1") -> str:
    """A Script Task ALONGSIDE otherwise-perfectly-valid Service/User Tasks.

    If the Script Task check did not exist, every other reference here would
    resolve fine — so a refusal on this document proves the refusal is
    genuinely about the Script Task, not some unrelated validation failure.
    """
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<bpmn:definitions xmlns:bpmn="{BPMN_NS}" xmlns:twoa="http://2ndactcapital.com/bpmn/ext" '
        f'id="D_{suffix}" targetNamespace="http://2ndactcapital.com/bpmn">'
        f'<bpmn:process id="proc_{suffix}" isExecutable="true">'
        '<bpmn:startEvent id="Start_1"><bpmn:outgoing>f1</bpmn:outgoing></bpmn:startEvent>'
        '<bpmn:serviceTask id="Svc_1" name="Show deals">'
        f'<bpmn:extensionElements><twoa:governance actionRegistryKey="{action_key}"/></bpmn:extensionElements>'
        '<bpmn:incoming>f1</bpmn:incoming><bpmn:outgoing>f2</bpmn:outgoing></bpmn:serviceTask>'
        f'<bpmn:scriptTask id="{script_id}" name="Evil script" scriptFormat="Python">'
        '<bpmn:incoming>f2</bpmn:incoming><bpmn:outgoing>f3</bpmn:outgoing>'
        '<bpmn:script>import os; os.system("id")</bpmn:script>'
        '</bpmn:scriptTask>'
        '<bpmn:userTask id="User_1" name="Review">'
        f'<bpmn:extensionElements><twoa:governance assignedRoleProfileId="{profile_id}"/></bpmn:extensionElements>'
        '<bpmn:incoming>f3</bpmn:incoming><bpmn:outgoing>f4</bpmn:outgoing></bpmn:userTask>'
        '<bpmn:endEvent id="End_1"><bpmn:incoming>f4</bpmn:incoming></bpmn:endEvent>'
        '<bpmn:sequenceFlow id="f1" sourceRef="Start_1" targetRef="Svc_1"/>'
        f'<bpmn:sequenceFlow id="f2" sourceRef="Svc_1" targetRef="{script_id}"/>'
        f'<bpmn:sequenceFlow id="f3" sourceRef="{script_id}" targetRef="User_1"/>'
        '<bpmn:sequenceFlow id="f4" sourceRef="User_1" targetRef="End_1"/>'
        '</bpmn:process></bpmn:definitions>'
    )


def _minimal_legit_bpmn(suffix: str) -> str:
    """A trivially valid start->end process — used as the baseline "current
    version" for refusal proofs so we can assert it is byte-for-byte
    unchanged afterwards."""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<bpmn:definitions xmlns:bpmn="{BPMN_NS}" id="D_{suffix}" '
        'targetNamespace="http://2ndactcapital.com/bpmn">'
        f'<bpmn:process id="proc_{suffix}" isExecutable="true">'
        '<bpmn:startEvent id="s"><bpmn:outgoing>e1</bpmn:outgoing></bpmn:startEvent>'
        '<bpmn:endEvent id="e"><bpmn:incoming>e1</bpmn:incoming></bpmn:endEvent>'
        '<bpmn:sequenceFlow id="e1" sourceRef="s" targetRef="e"/>'
        '</bpmn:process></bpmn:definitions>'
    )


def _good_bpmn_all_types(suffix: str, action_key: str, profile_id) -> str:
    """Legitimate BPMN exercising Service / Send / Gateway / User elements."""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<bpmn:definitions xmlns:bpmn="{BPMN_NS}" xmlns:twoa="http://2ndactcapital.com/bpmn/ext" '
        f'id="D_{suffix}" targetNamespace="http://2ndactcapital.com/bpmn">'
        f'<bpmn:process id="proc_{suffix}" isExecutable="true">'
        '<bpmn:startEvent id="Start_1"><bpmn:outgoing>f1</bpmn:outgoing></bpmn:startEvent>'
        '<bpmn:serviceTask id="Svc_1" name="Show deals">'
        f'<bpmn:extensionElements><twoa:governance actionRegistryKey="{action_key}"/></bpmn:extensionElements>'
        '<bpmn:incoming>f1</bpmn:incoming><bpmn:outgoing>f2</bpmn:outgoing></bpmn:serviceTask>'
        '<bpmn:sendTask id="Send_1" name="Notify">'
        '<bpmn:incoming>f2</bpmn:incoming><bpmn:outgoing>f3</bpmn:outgoing></bpmn:sendTask>'
        '<bpmn:exclusiveGateway id="Gw_1" name="Route">'
        '<bpmn:incoming>f3</bpmn:incoming><bpmn:outgoing>f4</bpmn:outgoing></bpmn:exclusiveGateway>'
        '<bpmn:userTask id="User_1" name="Review">'
        f'<bpmn:extensionElements><twoa:governance assignedRoleProfileId="{profile_id}"/></bpmn:extensionElements>'
        '<bpmn:incoming>f4</bpmn:incoming><bpmn:outgoing>f5</bpmn:outgoing></bpmn:userTask>'
        '<bpmn:endEvent id="End_1"><bpmn:incoming>f5</bpmn:incoming></bpmn:endEvent>'
        '<bpmn:sequenceFlow id="f1" sourceRef="Start_1" targetRef="Svc_1"/>'
        '<bpmn:sequenceFlow id="f2" sourceRef="Svc_1" targetRef="Send_1"/>'
        '<bpmn:sequenceFlow id="f3" sourceRef="Send_1" targetRef="Gw_1"/>'
        '<bpmn:sequenceFlow id="f4" sourceRef="Gw_1" targetRef="User_1"/>'
        '<bpmn:sequenceFlow id="f5" sourceRef="User_1" targetRef="End_1"/>'
        '</bpmn:process></bpmn:definitions>'
    )


def _runnable_bpmn(suffix: str, action_key: str) -> str:
    """No User Task — every element auto-completes, so a real run reaches
    'completed' status without needing a maker-checker resume step."""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<bpmn:definitions xmlns:bpmn="{BPMN_NS}" xmlns:twoa="http://2ndactcapital.com/bpmn/ext" '
        f'id="D_{suffix}" targetNamespace="http://2ndactcapital.com/bpmn">'
        f'<bpmn:process id="proc_{suffix}" isExecutable="true">'
        '<bpmn:startEvent id="Start_1"><bpmn:outgoing>f1</bpmn:outgoing></bpmn:startEvent>'
        '<bpmn:serviceTask id="Svc_1" name="Show deals">'
        f'<bpmn:extensionElements><twoa:governance actionRegistryKey="{action_key}"/></bpmn:extensionElements>'
        '<bpmn:incoming>f1</bpmn:incoming><bpmn:outgoing>f2</bpmn:outgoing></bpmn:serviceTask>'
        '<bpmn:sendTask id="Send_1" name="Notify">'
        '<bpmn:incoming>f2</bpmn:incoming><bpmn:outgoing>f3</bpmn:outgoing></bpmn:sendTask>'
        '<bpmn:endEvent id="End_1"><bpmn:incoming>f3</bpmn:incoming></bpmn:endEvent>'
        '<bpmn:sequenceFlow id="f1" sourceRef="Start_1" targetRef="Svc_1"/>'
        '<bpmn:sequenceFlow id="f2" sourceRef="Svc_1" targetRef="Send_1"/>'
        '<bpmn:sequenceFlow id="f3" sourceRef="Send_1" targetRef="End_1"/>'
        '</bpmn:process></bpmn:definitions>'
    )


def _legacy_businessrule_bpmn(suffix: str, action_key: str) -> str:
    """Simulates a PRE-EXISTING stored row (businessRuleTask + serviceTask),
    written directly to the database, never touched by either writer — the
    regression check that the new scriptTask OVERRIDE_PARSER_CLASSES entry
    didn't disturb the existing businessRuleTask one (same dict)."""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<bpmn:definitions xmlns:bpmn="{BPMN_NS}" xmlns:twoa="http://2ndactcapital.com/bpmn/ext" '
        f'id="D_{suffix}" targetNamespace="http://2ndactcapital.com/bpmn">'
        f'<bpmn:process id="proc_{suffix}" isExecutable="true">'
        '<bpmn:startEvent id="Start_1"><bpmn:outgoing>f1</bpmn:outgoing></bpmn:startEvent>'
        '<bpmn:serviceTask id="Svc_1" name="Show deals">'
        f'<bpmn:extensionElements><twoa:governance actionRegistryKey="{action_key}"/></bpmn:extensionElements>'
        '<bpmn:incoming>f1</bpmn:incoming><bpmn:outgoing>f2</bpmn:outgoing></bpmn:serviceTask>'
        '<bpmn:businessRuleTask id="Rule_1" name="Classify">'
        '<bpmn:incoming>f2</bpmn:incoming><bpmn:outgoing>f3</bpmn:outgoing></bpmn:businessRuleTask>'
        '<bpmn:endEvent id="End_1"><bpmn:incoming>f3</bpmn:incoming></bpmn:endEvent>'
        '<bpmn:sequenceFlow id="f1" sourceRef="Start_1" targetRef="Svc_1"/>'
        '<bpmn:sequenceFlow id="f2" sourceRef="Svc_1" targetRef="Rule_1"/>'
        '<bpmn:sequenceFlow id="f3" sourceRef="Rule_1" targetRef="End_1"/>'
        '</bpmn:process></bpmn:definitions>'
    )


# ═══════════════════════════════════════════════════════════════════════════
# Teardown / seed — raw connection, explicit is_super_admin GUC per
# transaction (CLAUDE.md's platform_scope() convention). audit_log /
# assistant_activities / agent_proposals are children of users and MUST be
# cleared BEFORE fixture users are deleted, or teardown fails with a
# ForeignKeyViolationError — often only on the SECOND run, once a first run
# has actually left one of those rows behind.
# ═══════════════════════════════════════════════════════════════════════════
async def _super(conn):
    await conn.execute("SELECT set_config('app.is_super_admin', 'true', true)")


# Every ad hoc read of an RLS-protected table (workflow_definitions/
# workflow_versions/workflow_steps) on the raw `conn` — as opposed to calls
# that go through `pool` (save_new_version/generate_workflow/start_workflow_run,
# which apply RLS context themselves via set_rls_context) — MUST set RLS
# context first. CLAUDE.md's documented trap: a raw connection with no
# context set returns ZERO ROWS SILENTLY on a read, not an error, which reads
# exactly like "the fixture row doesn't exist" when it actually does. These
# verification reads deliberately use the super-admin bypass (not an org
# GUC) so they see the true state regardless of which org is under test.
async def _read_rls(conn, query, *args):
    async with conn.transaction():
        await _super(conn)
        return await conn.fetch(query, *args)


async def _read_rls_row(conn, query, *args):
    async with conn.transaction():
        await _super(conn)
        return await conn.fetchrow(query, *args)


async def _read_rls_val(conn, query, *args):
    async with conn.transaction():
        await _super(conn)
        return await conn.fetchval(query, *args)


async def teardown(conn):
    async with conn.transaction():
        await _super(conn)
        # Resolve stray definition ids FIRST (e.g. a generator-path definition
        # that should never have been created — its id is chosen by
        # generate_workflow itself, unknown ahead of time) so the cascade
        # below covers them too, not just the known fixture ids. Otherwise a
        # failed refusal would leak orphaned workflow_versions/workflow_steps
        # rows that the name-matched workflow_definitions DELETE alone
        # wouldn't reach.
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
            "DELETE FROM workflow_definitions WHERE id = ANY($1::uuid[])",
            all_ids,
        )
        await conn.execute(
            "DELETE FROM agent_proposals WHERE org_id = ANY($1::uuid[])", ALL_FIXTURE_ORGS
        )
        await conn.execute(
            "DELETE FROM assistant_activities WHERE user_id = ANY($1::uuid[]) "
            "OR proposed_by = ANY($1::uuid[]) OR approved_by = ANY($1::uuid[])",
            ALL_FIXTURE_USERS,
        )
        await conn.execute(
            "DELETE FROM audit_log WHERE user_id = ANY($1::uuid[])", ALL_FIXTURE_USERS
        )
        await conn.execute("UPDATE users SET profile_id = NULL WHERE id = ANY($1::uuid[])", ALL_FIXTURE_USERS)
        await conn.execute(
            """DELETE FROM profile_permissions WHERE profile_id IN
                 (SELECT id FROM profiles WHERE org_id = $1 AND name = $2)""",
            ORG_B, FIXTURE_PROFILE_B,
        )
        await conn.execute(
            "DELETE FROM profiles WHERE org_id = $1 AND name = $2 AND is_seed = false",
            ORG_B, FIXTURE_PROFILE_B,
        )
        await conn.execute("DELETE FROM users WHERE id = ANY($1::uuid[])", ALL_FIXTURE_USERS)
        await conn.execute("DELETE FROM organizations WHERE id = ANY($1::uuid[])", ALL_FIXTURE_ORGS)


async def seed(conn) -> dict:
    """Returns {"profile_a": <uuid>, "profile_b": <uuid>}."""
    async with conn.transaction():
        await _super(conn)

        await conn.execute(
            """INSERT INTO organizations (id, name, slug) VALUES ($1, $2, $3)
               ON CONFLICT (id) DO NOTHING""",
            ORG_B, "ScriptTaskRefusal Fixture Org B", "scripttaskrefusal-fixture-org-b",
        )

        profile_a = await conn.fetchval(
            "SELECT id FROM profiles WHERE org_id = $1 AND name = $2", ORG_A, ORG_ADMIN_PROFILE
        )
        profile_b = await conn.fetchval(
            """INSERT INTO profiles (org_id, name, description, is_seed)
               VALUES ($1, $2, 'scripttaskrefusal verify', false)
               ON CONFLICT (org_id, name) DO UPDATE SET updated_at = now()
               RETURNING id""",
            ORG_B, FIXTURE_PROFILE_B,
        )
        await conn.execute(
            """INSERT INTO profile_permissions (org_id, profile_id, permission_key)
               VALUES ($1, $2, 'author_workflows') ON CONFLICT (profile_id, permission_key) DO NOTHING""",
            ORG_B, profile_b,
        )

        async def mk_user(uid, org_id, role, profile_id):
            sub = _sub(uid)
            await conn.execute(
                """INSERT INTO users (id, org_id, email, full_name, auth0_sub, role, profile_id, is_active)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, true)
                   ON CONFLICT (auth0_sub) DO UPDATE
                     SET org_id = EXCLUDED.org_id, role = EXCLUDED.role,
                         profile_id = EXCLUDED.profile_id, is_active = true""",
                uid, org_id, f"{sub}@test.local", sub, sub, role, profile_id,
            )

        await mk_user(U_CREATOR_A, ORG_A, "member", None)
        await mk_user(U_API_A, ORG_A, "org_admin", profile_a)
        await mk_user(U_CREATOR_B, ORG_B, "member", profile_b)

        # DEF_HANDEDIT / DEF_API / DEF_ORGB start from a trivially valid v1 so
        # a REFUSED save-attempt's "unchanged afterwards" assertion has a
        # concrete, known-good baseline to diff against.
        for def_id, ver_id, org_id, suffix, creator in (
            (DEF_HANDEDIT, VER_HANDEDIT, ORG_A, "handedit", U_CREATOR_A),
            (DEF_API, VER_API, ORG_A, "api", U_API_A),
            (DEF_ORGB, VER_ORGB, ORG_B, "orgb", U_CREATOR_B),
        ):
            await conn.execute(
                """INSERT INTO workflow_definitions (id, org_id, name, description, created_by)
                   VALUES ($1, $2, $3, 'scripttaskrefusal fixture', $4)
                   ON CONFLICT (id) DO NOTHING""",
                def_id, org_id, f"{NAME_MARKER} {suffix}", creator,
            )
            await conn.execute(
                """INSERT INTO workflow_versions
                     (id, workflow_definition_id, org_id, version_number, bpmn_xml,
                      change_summary, is_current, created_by)
                   VALUES ($1, $2, $3, 1, $4, 'v1 baseline', true, $5)
                   ON CONFLICT (id) DO NOTHING""",
                ver_id, def_id, org_id, _minimal_legit_bpmn(suffix), creator,
            )

        # DEF_LEGIT starts from the same trivial baseline; the actual "saves
        # correctly" proof is a REAL save_new_version() call in main_async.
        await conn.execute(
            """INSERT INTO workflow_definitions (id, org_id, name, description, created_by)
               VALUES ($1, $2, $3, 'scripttaskrefusal fixture', $4)
               ON CONFLICT (id) DO NOTHING""",
            DEF_LEGIT, ORG_A, f"{NAME_MARKER} legit", U_CREATOR_A,
        )
        await conn.execute(
            """INSERT INTO workflow_versions
                 (id, workflow_definition_id, org_id, version_number, bpmn_xml,
                  change_summary, is_current, created_by)
               VALUES ($1, $2, $3, 1, $4, 'v1 baseline', true, $5)
               ON CONFLICT (id) DO NOTHING""",
            VER_LEGIT, DEF_LEGIT, ORG_A, _minimal_legit_bpmn("legit"), U_CREATOR_A,
        )

        # DEF_RUNNABLE and DEF_LEGACY are seeded with their REAL current
        # version directly (simulating rows that pre-date this sprint /
        # were never routed through either writer) — workflow_steps for
        # each is derived via the real deriver below, not by hand.
        runnable_xml = _runnable_bpmn("runnable", READ_ACTION)
        legacy_xml = _legacy_businessrule_bpmn("legacy", READ_ACTION)
        await conn.execute(
            """INSERT INTO workflow_definitions (id, org_id, name, description, created_by)
               VALUES ($1, $2, $3, 'scripttaskrefusal fixture', $4)
               ON CONFLICT (id) DO NOTHING""",
            DEF_RUNNABLE, ORG_A, f"{NAME_MARKER} runnable", U_CREATOR_A,
        )
        await conn.execute(
            """INSERT INTO workflow_versions
                 (id, workflow_definition_id, org_id, version_number, bpmn_xml,
                  change_summary, is_current, created_by)
               VALUES ($1, $2, $3, 1, $4, 'runnable fixture', true, $5)
               ON CONFLICT (id) DO NOTHING""",
            VER_RUNNABLE, DEF_RUNNABLE, ORG_A, runnable_xml, U_CREATOR_A,
        )
        await conn.execute(
            """INSERT INTO workflow_definitions (id, org_id, name, description, created_by)
               VALUES ($1, $2, $3, 'scripttaskrefusal fixture', $4)
               ON CONFLICT (id) DO NOTHING""",
            DEF_LEGACY, ORG_A, f"{NAME_MARKER} legacy", U_CREATOR_A,
        )
        await conn.execute(
            """INSERT INTO workflow_versions
                 (id, workflow_definition_id, org_id, version_number, bpmn_xml,
                  change_summary, is_current, created_by)
               VALUES ($1, $2, $3, 1, $4, 'pre-existing legacy fixture', true, $5)
               ON CONFLICT (id) DO NOTHING""",
            VER_LEGACY, DEF_LEGACY, ORG_A, legacy_xml, U_CREATOR_A,
        )
        await derive_and_store_steps(conn, VER_RUNNABLE, ORG_A, runnable_xml)
        await derive_and_store_steps(conn, VER_LEGACY, ORG_A, legacy_xml)

    return {"profile_a": profile_a, "profile_b": profile_b}


# ═══════════════════════════════════════════════════════════════════════════
# Task 1 — discovery, reported live
# ═══════════════════════════════════════════════════════════════════════════
async def task1_report(conn) -> None:
    print("\n── TASK 1 — discovery findings (live) ──")

    # 1a — the live database check.
    hits = []
    async with conn.transaction():
        await _super(conn)
        full_rows = await conn.fetch(
            """
            SELECT wv.id AS version_id, wv.workflow_definition_id, wv.org_id,
                   wv.version_number, wv.is_current, wd.name, wv.bpmn_xml
            FROM workflow_versions wv
            JOIN workflow_definitions wd ON wd.id = wv.workflow_definition_id
            ORDER BY wv.org_id, wd.name, wv.version_number
            """
        )
    for r in full_rows:
        ids = find_script_tasks(r["bpmn_xml"])
        if ids:
            hits.append((r["name"], str(r["org_id"]), r["version_number"], ids))
    print(f"[1a] scanned {len(full_rows)} live workflow_versions row(s) for any bpmn:scriptTask element.")
    for r in full_rows:
        print(f"     - def={r['name']!r} org_id={r['org_id']} v={r['version_number']} "
              f"current={r['is_current']}")
    if hits:
        for name, org_id, vnum, ids in hits:
            find(
                f"[1a] INCIDENT — stored Script Task found in def={name!r} org_id={org_id} v={vnum}",
                f"element ids: {ids}",
            )
        check(False, "[1a] live database is CLEAN of stored Script Tasks", f"{len(hits)} row(s) contained one")
    else:
        check(True, "[1a] live database is CLEAN — zero stored workflow_versions.bpmn_xml rows "
                     "contain a bpmn:scriptTask element (any namespace prefix); this sprint is "
                     "PREVENTIVE, not remedial — nothing to remediate or delete",
              f"{len(full_rows)} row(s) scanned")

    # 1b — one shared validator, not two.
    editor_src = inspect.getsource(editor.save_new_version)
    generate_once_src = inspect.getsource(nlgen._generate_once)
    validate_bpmn_src = inspect.getsource(nlgen.validate_workflow_bpmn)
    editor_calls_shared = "validate_workflow_bpmn(" in editor_src
    generate_calls_validate = "_validate(" in generate_once_src
    editor_path_calls_validate = "_validate(" in validate_bpmn_src
    check(
        editor_calls_shared and generate_calls_validate and editor_path_calls_validate,
        "[1b] ONE shared validator: workflow_editor.save_new_version calls "
        "workflow_nl_generator.validate_workflow_bpmn, which — like the generator's own "
        "_generate_once — calls the same private _validate. Fixing _validate once closes "
        "the gap for BOTH writers; proven by source inspection, not assumed.",
        f"editor calls validate_workflow_bpmn={editor_calls_shared}, "
        f"_generate_once calls _validate={generate_calls_validate}, "
        f"validate_workflow_bpmn calls _validate={editor_path_calls_validate}",
    )
    check(
        "find_script_tasks(" in inspect.getsource(nlgen._validate),
        "[1b] _validate itself calls find_script_tasks BEFORE attempting the SpiffWorkflow "
        "parse — the refusal is loud and specific, not folded into a generic parse error",
    )

    # 1c — same OVERRIDE_PARSER_CLASSES mechanism as _BusinessRuleTaskParser.
    parser = eng._make_bpmn_parser()
    overrides = parser.OVERRIDE_PARSER_CLASSES
    script_tag = full_tag("scriptTask")
    rule_tag = full_tag("businessRuleTask")
    check(
        script_tag in overrides
        and overrides[script_tag][0] is eng._ScriptTaskParser
        and rule_tag in overrides
        and overrides[rule_tag][0] is eng._BusinessRuleTaskParser,
        "[1c] _ScriptTaskParser is registered via the SAME OVERRIDE_PARSER_CLASSES dict, "
        "the same full_tag() helper, and the same TaskParser base class as the existing "
        "_BusinessRuleTaskParser — not a second, unrelated mechanism",
        f"overrides keys include scriptTask={script_tag in overrides}, "
        f"businessRuleTask={rule_tag in overrides}",
    )

    # 1d — the bpmn-js palette: reported, never treated as enforcement.
    jsx_path = HERE.parents[2] / "web" / "components" / "admin" / "WorkflowDiagramEditor.jsx"
    jsx_src = jsx_path.read_text() if jsx_path.exists() else ""
    has_guard = (
        "commandStack.shape.create.postExecute" in jsx_src
        and "commandStack.shape.replace.postExecute" in jsx_src
        and "bpmn:ScriptTask" in jsx_src
    )
    find(
        "[1d] the bpmn-js palette cannot be the enforcement boundary — a hand-crafted XML "
        "POST bypasses any client-side restriction entirely (proven by Task 3's assertion "
        "below). It CAN be trimmed for usability.",
        f"WorkflowDiagramEditor.jsx contains a create/replace undo-guard for bpmn:ScriptTask: "
        f"{has_guard} (path checked: {jsx_path})",
    )
    check(
        has_guard,
        "[1d] the client-side usability guard is present in WorkflowDiagramEditor.jsx "
        "(non-enforcing — Task 3 below proves the server refuses regardless)",
    )


# ═══════════════════════════════════════════════════════════════════════════
async def main_async() -> int:
    from services.database import close_pool, get_pool, reset_rls_context, set_rls_context

    dsn = await bootstrap_async()
    if not dsn:
        print("[SKIP] no working DATABASE_URL — nothing can be proven")
        return 2

    register_all()
    conn = await asyncpg.connect(dsn, statement_cache_size=0, ssl="require")
    try:
        await teardown(conn)
        await task1_report(conn)

        print("\n── Fixtures ──")
        profiles = await seed(conn)
        check(
            profiles["profile_a"] is not None,
            "the ORG_A fixture user holds the REAL seeded 'Org Admin' profile "
            "(author_workflows) — not a bespoke test profile",
            f"profile_a={profiles['profile_a']}",
        )
        check(
            profiles["profile_b"] is not None,
            "ORG_B's fixture profile was created and grants author_workflows directly",
            f"profile_b={profiles['profile_b']}",
        )

        await close_pool()
        pool = await get_pool()

        # ── Task 2 — hand-edit path refuses, names the element, persists nothing ──
        print("\n── [Y] Hand-edit save (workflow_editor.save_new_version) refuses a Script Task ──")
        before = await _read_rls_row(
            conn,
            "SELECT version_number, is_current, bpmn_xml FROM workflow_versions WHERE id = $1",
            VER_HANDEDIT,
        )
        before_count = await _read_rls_val(
            conn, "SELECT count(*) FROM workflow_versions WHERE workflow_definition_id = $1", DEF_HANDEDIT
        )
        check(
            before is not None,
            "the DEF_HANDEDIT fixture's baseline v1 version was actually seeded and is "
            "readable with proper RLS context — the 'unchanged afterwards' check below is "
            "only meaningful if this is a REAL row, not a None-vs-None vacuous pass",
            f"before={before}",
        )
        script_xml = _script_bpmn("handedit", script_id="HandEditEvil")
        tokens = set_rls_context(ORG_A, False)
        raised = None
        try:
            await save_new_version(
                pool, definition_id=DEF_HANDEDIT, org_id=ORG_A, bpmn_xml=script_xml,
                created_by=U_CREATOR_A, change_summary="attempted script task",
            )
        except WorkflowValidationError as exc:
            raised = exc
        finally:
            reset_rls_context(tokens)
        check(
            raised is not None,
            "save_new_version raised WorkflowValidationError on a Script Task BPMN",
            f"raised={raised}",
        )
        check(
            raised is not None and "HandEditEvil" in str(raised) and "Script Task" in str(raised),
            "the error is LOUD and SPECIFIC — names the exact element id, not a generic "
            "validation failure",
            f"detail={raised}",
        )
        after = await _read_rls_row(
            conn,
            "SELECT version_number, is_current, bpmn_xml FROM workflow_versions WHERE id = $1",
            VER_HANDEDIT,
        )
        after_count = await _read_rls_val(
            conn, "SELECT count(*) FROM workflow_versions WHERE workflow_definition_id = $1", DEF_HANDEDIT
        )
        check(
            before_count == after_count == 1
            and before is not None and after is not None and dict(before) == dict(after),
            "the target definition's current version is BYTE-FOR-BYTE unchanged afterwards "
            "— nothing was persisted on refusal (re-read from an independent query, real "
            "rows on both sides, not a None-vs-None vacuous comparison)",
            f"row count before={before_count} after={after_count}; "
            f"before is None={before is None}; after is None={after is None}; "
            f"version unchanged={before is not None and after is not None and dict(before) == dict(after)}",
        )

        # ── Task 2 (independent) — the generator path refuses too ──────────
        print("\n── [Y] Generator path (generate_workflow) refuses a Script Task, independently ──")
        _install_fake_anthropic()
        gen_script_xml = _script_bpmn_with_valid_refs(
            "gen", READ_ACTION, profiles["profile_a"], script_id="GenEvil"
        )
        defs_before = await _read_rls_val(
            conn,
            "SELECT count(*) FROM workflow_definitions WHERE org_id = $1 AND name LIKE $2",
            ORG_A, f"{NAME_MARKER}%",
        )
        _RESPONSE_QUEUE.clear()
        _RESPONSE_QUEUE.append(gen_script_xml)  # first attempt
        _RESPONSE_QUEUE.append(gen_script_xml)  # error-correction retry
        tokens = set_rls_context(ORG_A, False)
        gen_raised = None
        try:
            await generate_workflow(
                pool, org_id=ORG_A, description="A process with a script step.",
                created_by=U_CREATOR_A, name=f"{NAME_MARKER} generator",
            )
        except WorkflowGenerationError as exc:
            gen_raised = exc
        finally:
            reset_rls_context(tokens)
        check(
            gen_raised is not None,
            "generate_workflow raised WorkflowGenerationError on a model response containing "
            "a Script Task — proven end to end (real retry loop, real hermetic AI call), "
            "not assumed from 1b's shared _validate",
            f"raised={gen_raised}",
        )
        check(
            gen_raised is not None and "GenEvil" in str(gen_raised) and "Script Task" in str(gen_raised),
            "the generator's error also names the exact element id",
            f"detail={gen_raised}",
        )
        defs_after = await _read_rls_val(
            conn,
            "SELECT count(*) FROM workflow_definitions WHERE org_id = $1 AND name LIKE $2",
            ORG_A, f"{NAME_MARKER}%",
        )
        check(
            defs_before == defs_after,
            "no new workflow_definitions row was created by the refused generation attempt",
            f"count before={defs_before} after={defs_after}",
        )

        # ── Task 3 — a hand-crafted XML POST straight to the REAL API ──────
        print("\n── [Y] Direct API POST bypassing any client-side restriction — still refused ──")
        api_before = await _read_rls_row(
            conn, "SELECT version_number, bpmn_xml FROM workflow_versions WHERE id = $1", VER_API
        )
        check(
            api_before is not None,
            "the DEF_API fixture's baseline v1 version was actually seeded and is readable "
            "with proper RLS context",
            f"api_before={api_before}",
        )
        # TestClient runs the ASGI app on ITS OWN event loop, alive in a
        # separate thread only for the duration of the `with` block below
        # (run_in_executor). services.database.get_pool()'s asyncpg Pool is
        # loop-bound at creation time, so the pool this coroutine already
        # holds (bound to THIS loop) must not be handed to that request.
        # close_pool() here forces the app's very first get_pool() call
        # inside the TestClient request to create a fresh pool correctly
        # bound to the TestClient's own loop; closing and re-acquiring
        # afterwards restores a pool bound back to THIS loop for Tasks 4-6.
        await close_pool()
        loop = asyncio.get_running_loop()
        status, detail = await loop.run_in_executor(None, _direct_api_post_test)
        await close_pool()
        pool = await get_pool()
        api_after = await _read_rls_row(
            conn, "SELECT version_number, bpmn_xml FROM workflow_versions WHERE id = $1", VER_API
        )
        check(
            status == 422,
            "POST /api/v1/admin/workflows/{id}/versions with a hand-crafted Script Task XML "
            "returns 422 — refused at the real HTTP boundary, no client involved at all",
            f"status={status}",
        )
        check(
            "ApiEvil" in detail and "Script Task" in detail,
            "the HTTP error body names the exact element id — this is the assertion that "
            "proves enforcement is server-side, not merely a client-side nicety",
            f"detail={detail[:300]}",
        )
        check(
            api_before is not None and api_after is not None and dict(api_before) == dict(api_after),
            "the definition's current version is unchanged after the direct API bypass attempt "
            "(real before-row vs real after-row, not a None-vs-None vacuous comparison)",
            f"before is None={api_before is None}; after is None={api_after is None}; "
            f"unchanged={api_before is not None and api_after is not None and dict(api_before) == dict(api_after)}",
        )

        # ── Task 4 — legitimate BPMN still saves; a real run still completes ──
        print("\n── [Y] No regression: legitimate BPMN saves; a real run completes ──")
        tokens = set_rls_context(ORG_A, False)
        legit_result = None
        legit_err = None
        try:
            legit_result = await save_new_version(
                pool, definition_id=DEF_LEGIT, org_id=ORG_A,
                bpmn_xml=_good_bpmn_all_types("legit", READ_ACTION, profiles["profile_a"]),
                created_by=U_CREATOR_A, change_summary="legit save, no script task",
            )
        except WorkflowValidationError as exc:
            legit_err = exc
        finally:
            reset_rls_context(tokens)
        check(
            legit_result is not None,
            "a legitimate BPMN with Service, Send, Gateway and User elements saves "
            "successfully via the SAME validator that refuses Script Tasks",
            f"result={legit_result} err={legit_err}",
        )
        if legit_result is not None:
            step_rows = await _read_rls(
                conn,
                "SELECT step_type FROM workflow_steps WHERE workflow_version_id = $1",
                legit_result["workflow_version_id"],
            )
            step_types = sorted(r["step_type"] for r in step_rows)
            check(
                step_types == ["send", "service", "user"],
                "workflow_steps derived exactly one row per actionable element "
                "(service/send/user), and the Gateway correctly produced no row",
                f"step_types={step_types}",
            )

        tokens = set_rls_context(ORG_A, False)
        try:
            run_result = await start_workflow_run(
                pool, VER_RUNNABLE, ORG_A, {"note": "verify"}, U_CREATOR_A,
            )
        finally:
            reset_rls_context(tokens)
        check(
            run_result["status"] == "completed",
            "a real run of a legitimate (Script-Task-free) BPMN drives all the way to "
            "'completed' via the real SpiffWorkflow engine — no regression from either "
            "the shared-validator change or the parser-level defense in depth",
            f"run_result={run_result}",
        )

        # ── Task 5 — an existing (pre-sprint-style) stored definition ──────
        print("\n── [Y] An existing stored definition still parses and runs unchanged ──")
        legacy_row = await _read_rls_row(
            conn, "SELECT bpmn_xml FROM workflow_versions WHERE id = $1", VER_LEGACY
        )
        check(
            legacy_row is not None,
            "the DEF_LEGACY fixture (pre-existing businessRuleTask + serviceTask definition) "
            "was actually seeded and is readable with proper RLS context",
            f"legacy_row is None={legacy_row is None}",
        )
        if legacy_row is not None:
            spec, proc_id = parse_bpmn(legacy_row["bpmn_xml"])
            derived = derive_steps(legacy_row["bpmn_xml"])
            check(
                spec is not None and {"service", "business_rule"} == {s["step_type"] for s in derived},
                "the pre-existing businessRuleTask + serviceTask definition still parses via "
                "parse_bpmn and still derives the same step types as before this sprint — "
                "proves the new scriptTask OVERRIDE_PARSER_CLASSES entry didn't disturb the "
                "existing businessRuleTask entry in the same dict",
                f"process_id={proc_id}, step_types={sorted(s['step_type'] for s in derived)}",
            )
            tokens = set_rls_context(ORG_A, False)
            try:
                legacy_run = await start_workflow_run(
                    pool, VER_LEGACY, ORG_A, {}, U_CREATOR_A,
                )
            finally:
                reset_rls_context(tokens)
            check(
                legacy_run["status"] == "completed",
                "the existing stored definition still runs to completion unchanged",
                f"legacy_run={legacy_run}",
            )
        else:
            check(False, "the existing-stored-definition parse/derive/run proofs were "
                          "SKIPPED because legacy_row could not be read — see the finding above")
            check(False, "the existing-stored-definition run-to-completion proof was "
                          "SKIPPED because legacy_row could not be read — see the finding above")

        # ── Task 6 — cross-org: refusal applies everywhere ──────────────────
        print("\n── [Y] Cross-org: refusal applies in ORG_B too, not just the fixture's org ──")
        orgb_before = await _read_rls_row(
            conn, "SELECT version_number, bpmn_xml FROM workflow_versions WHERE id = $1", VER_ORGB
        )
        check(
            orgb_before is not None,
            "the DEF_ORGB fixture's baseline v1 version was actually seeded and is readable "
            "with proper RLS context",
            f"orgb_before={orgb_before}",
        )
        tokens = set_rls_context(ORG_B, False)
        orgb_raised = None
        try:
            await save_new_version(
                pool, definition_id=DEF_ORGB, org_id=ORG_B,
                bpmn_xml=_script_bpmn("orgb", script_id="OrgBEvil"),
                created_by=U_CREATOR_B, change_summary="attempted script task in org B",
            )
        except WorkflowValidationError as exc:
            orgb_raised = exc
        finally:
            reset_rls_context(tokens)
        check(
            orgb_raised is not None and "OrgBEvil" in str(orgb_raised),
            "a SECOND, independent org is refused identically, naming the element — the "
            "refusal is not scoped to ORG_A alone",
            f"raised={orgb_raised}",
        )
        orgb_after = await _read_rls_row(
            conn, "SELECT version_number, bpmn_xml FROM workflow_versions WHERE id = $1", VER_ORGB
        )
        check(
            orgb_before is not None and orgb_after is not None and dict(orgb_before) == dict(orgb_after),
            "ORG_B's definition is unchanged after its own refused attempt (real before-row "
            "vs real after-row)",
            f"before is None={orgb_before is None}; after is None={orgb_after is None}; "
            f"unchanged={orgb_before is not None and orgb_after is not None and dict(orgb_before) == dict(orgb_after)}",
        )
        tokens = set_rls_context(ORG_B, False)
        orgb_legit = None
        try:
            orgb_legit = await save_new_version(
                pool, definition_id=DEF_ORGB, org_id=ORG_B,
                bpmn_xml=_minimal_legit_bpmn("orgb-legit"),
                created_by=U_CREATOR_B, change_summary="legit save in org B",
            )
        finally:
            reset_rls_context(tokens)
        check(
            orgb_legit is not None,
            "ORG_B is not just broadly broken — a legitimate save in the SAME org still "
            "succeeds right after the refused attempt",
            f"orgb_legit={orgb_legit}",
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
                            + (SELECT count(*) FROM assistant_activities WHERE user_id = ANY($3::uuid[]))
                            + (SELECT count(*) FROM audit_log WHERE user_id = ANY($3::uuid[]))
                            + (SELECT count(*) FROM users WHERE id = ANY($3::uuid[]))
                            + (SELECT count(*) FROM organizations WHERE id = ANY($2::uuid[]))""",
                    ALL_DEF_IDS, ALL_FIXTURE_ORGS, ALL_FIXTURE_USERS, f"%{NAME_MARKER}%",
                )
            check(
                leftovers == 0,
                "TEARDOWN: zero leftover fixture rows across workflow_definitions/versions/"
                "steps/runs, agent_proposals/assistant_activities/audit_log (cleared BEFORE "
                "users), and users/organizations",
                f"found {leftovers}",
            )
        finally:
            from services.database import close_pool as _close_pool
            await _close_pool()
            await conn.close()

    print(f"\n{'=' * 70}\nTOTAL: {_n_pass} PASS, {_n_fail} FAIL, {len(_finds)} FIND\n{'=' * 70}")
    return 0 if _ok else 1


# ═══════════════════════════════════════════════════════════════════════════
# TestClient — sync, driven off the running loop via run_in_executor (matches
# verify_workflowpermsfix.py: entered as a context manager, ONE client, or the
# app's module-global pool ends up bound to a dead event loop).
# ═══════════════════════════════════════════════════════════════════════════
def _direct_api_post_test():
    import main
    from starlette.testclient import TestClient

    main.verify_token = lambda _t: {
        "sub": _sub(U_API_A), "email": f"{_sub(U_API_A)}@test.local", "org_id": str(ORG_A),
    }
    client = TestClient(main.app, raise_server_exceptions=False)
    client.__enter__()
    try:
        res = client.post(
            f"/api/v1/admin/workflows/{DEF_API}/versions",
            headers=HEADERS,
            json={
                "bpmn_xml": _script_bpmn("api", script_id="ApiEvil"),
                "change_summary": "direct API bypass attempt",
            },
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
