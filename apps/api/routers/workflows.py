"""Admin endpoints: Workflow Manager library + diagram editor (Phase 3).

The HTTP surface for the first Workflow Manager UI. Thin wrappers over the
already-verified Phase 1/2/3 services:

  * ``GET  /admin/workflows``                     — library list (per definition:
        name, description, current version number, step/tier summary)
  * ``POST /admin/workflows``                      — create a brand-new definition
        from a natural-language description (Phase 2 ``generate_workflow``)
  * ``GET  /admin/workflows/{id}``                 — everything the diagram editor
        needs: the current version's BPMN, its derived steps, and the closed
        reference lists (real Profiles + action registry) that populate the
        properties-panel pickers
  * ``POST /admin/workflows/{id}/versions``        — save an edited BPMN as a NEW
        version + re-derive steps (Phase 3 ``save_new_version``)

Gate (Phase 5): GRANULAR, action-registry-based permissions — the blanket
``can_manage_org_settings`` gate used by Phases 3-4 is replaced by four
SEPARATELY grantable permission keys from the SOC ``permissions`` catalog,
enforced via ``services.profiles.user_has_permission`` (a user's Profile grants
∪ every assigned Permission Set):

  * ``author_workflows``            — library, editor detail, create, save-new-
        version, version history (authoring surface). Publishing is not a
        distinct step in this platform's generate-once + save-new-version model,
        so it is covered here.
  * ``view_workflow_runs``          — Run Console list + run drill-in.
  * ``configure_workflow_triggers`` — scheduler / triggers viewer.

A Super Admin (Ripasso platform staff) always passes and, on the read consoles,
still widens to all orgs. Everyone else — INCLUDING an Org Admin — must hold the
specific key; holding another admin-adjacent permission does not grant workflow
access. ``org_id`` is always resolved server-side from the authenticated context
and every query is scoped to it; it is NEVER read from a request body.
"""

import json
from datetime import datetime, timezone as _tz
from uuid import UUID

_UTC = _tz.utc

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, field_validator, model_validator

from routers.entities import get_org_id
from services.action_registry import REGISTRY
from services.database import get_pool
from services.profiles import user_has_permission
from services.rbac import is_super_admin, load_principal
from services.users import ensure_user
from services.workflow_editor import (
    WorkflowEditError,
    WorkflowValidationError,
    save_new_version,
)
from services.workflow_nl_generator import WorkflowGenerationError, generate_workflow
from services.workflow_schedule import (
    ScheduleError,
    describe_schedule,
    next_occurrences,
    parse_cron,
    resolve_timezone,
)

router = APIRouter(tags=["admin", "workflows"])


# --------------------------------------------------------------------------
# Models
# --------------------------------------------------------------------------
class WorkflowSummary(BaseModel):
    id: UUID
    name: str
    description: str | None = None
    current_version_number: int | None = None
    step_count: int = 0
    approval_step_count: int = 0


class WorkflowCreate(BaseModel):
    name: str | None = None
    description: str


class WorkflowStep(BaseModel):
    step_key: str
    step_type: str
    autonomy_tier: int
    action_registry_key: str | None = None
    assigned_role_profile_id: UUID | None = None
    display_name: str | None = None


class CurrentVersion(BaseModel):
    id: UUID
    version_number: int
    bpmn_xml: str


class ProfileOption(BaseModel):
    id: UUID
    name: str


class ActionOption(BaseModel):
    key: str
    access_type: str
    description: str | None = None


class WorkflowDetail(BaseModel):
    id: UUID
    name: str
    description: str | None = None
    current_version: CurrentVersion
    steps: list[WorkflowStep] = []
    profiles: list[ProfileOption] = []
    actions: list[ActionOption] = []


class VersionSave(BaseModel):
    bpmn_xml: str
    change_summary: str | None = None


def _validate_recurrence(
    *, schedule_cron, timezone_name, start_date, end_date, max_occurrences
) -> tuple[str, str]:
    """The scheduled-trigger rules, in ONE place. Raises ``ValueError``.

    Create, EDIT and PREVIEW all call this, so a schedule that create refuses is
    a schedule edit refuses and preview refuses, with the identical message.
    Before schedulerux these rules lived only inside ``TriggerCreate``'s model
    validator, which meant a PATCH endpoint written the obvious way would have
    been able to turn a valid stored trigger into an unrunnable one — the
    firing loop's ``ScheduleError`` path would then log it once per tick,
    forever, and nothing would ever run.

    The checks are in the same ORDER create used, because verify_schedulercore
    asserts on the specific message each bad payload comes back with.
    Returns the normalized ``(schedule_cron, timezone)`` pair.
    """
    if not (schedule_cron or "").strip():
        raise ValueError("schedule_cron is required when trigger_type='scheduled'")
    try:
        parse_cron(schedule_cron)
        resolve_timezone(timezone_name)
    except ScheduleError as exc:
        raise ValueError(str(exc)) from None
    if max_occurrences is not None and max_occurrences < 1:
        raise ValueError("max_occurrences must be a positive integer")
    if start_date is not None and end_date is not None and end_date < start_date:
        raise ValueError("end_date must not precede start_date")
    return schedule_cron.strip(), (timezone_name or "UTC").strip() or "UTC"


class TriggerCreate(BaseModel):
    """Create a trigger for a workflow definition.

    ORIGINALLY (Chancery Phase 7) this model accepted only three fields —
    ``workflow_definition_id``, ``event_type`` and ``is_active`` — so there was
    NO API path to create a schedule-type trigger at all. The one scheduled row
    in the deployed database had been inserted by a verify script. The scheduler
    sprint extends it, because otherwise the firing loop has nothing to fire and
    the CRUD UX sprint has no endpoint to call.

    ``trigger_type`` defaults to ``'event'``, so every existing caller — which
    sends no ``trigger_type`` at all — keeps its exact previous behaviour.

    The schedule fields are accepted ONLY when ``trigger_type='scheduled'``, and
    are rejected outright otherwise rather than silently ignored: a caller who
    posts a cron expression against an event trigger has a bug, and storing the
    value where nothing reads it is how ``schedule_cron`` became dead code in
    the first place.

    ``org_id`` / ``created_by`` always come from the authenticated context,
    never the body.
    """

    workflow_definition_id: UUID
    trigger_type: str = "event"
    event_type: str | None = None
    is_active: bool = True

    # Schedule-only fields.
    schedule_cron: str | None = None
    timezone: str = "UTC"
    start_date: datetime | None = None
    end_date: datetime | None = None
    max_occurrences: int | None = None

    @field_validator("trigger_type")
    @classmethod
    def _known_trigger_type(cls, value: str) -> str:
        value = (value or "").strip().lower()
        # 'scheduled', not 'schedule' — that is the value the deployed rows and
        # the trigger-list UI (WorkflowTriggerScheduler.jsx) both already use.
        if value not in ("event", "scheduled"):
            raise ValueError(
                "trigger_type must be 'event' or 'scheduled' "
                f"(got {value!r}); 'manual' triggers are not created through "
                "this endpoint"
            )
        return value

    @field_validator("start_date", "end_date")
    @classmethod
    def _aware_utc(cls, value: datetime | None) -> datetime | None:
        """A naive bound is read as UTC, explicitly.

        Left naive, asyncpg would hand it to a ``timestamptz`` column and
        Postgres would interpret it in the SERVER's timezone — so the same
        request would mean a different instant depending on where the database
        happens to be configured. The trigger's own IANA zone governs the
        recurrence; these two bounds are absolute instants and are stored as
        such."""
        if value is None or value.tzinfo is not None:
            return value
        return value.replace(tzinfo=_UTC)

    @model_validator(mode="after")
    def _coherent(self):
        schedule_only = {
            "schedule_cron": self.schedule_cron,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "max_occurrences": self.max_occurrences,
        }

        if self.trigger_type == "event":
            supplied = [k for k, v in schedule_only.items() if v is not None]
            if supplied:
                raise ValueError(
                    f"{', '.join(sorted(supplied))} may only be set when "
                    "trigger_type='scheduled'"
                )
            if self.event_type is None:
                self.event_type = EVENT_DOCUMENT_CONFIRMED
            return self

        # trigger_type == 'scheduled'
        if self.event_type is not None:
            raise ValueError(
                "event_type may not be set when trigger_type='scheduled'"
            )
        # Validate the cron expression and the IANA zone HERE, at the boundary.
        # An unparseable schedule stored as an active trigger is invisible until
        # the tick logs an error hours later; a 422 at write time is where the
        # author can still fix it. Shared with edit and preview — see
        # _validate_recurrence.
        self.schedule_cron, self.timezone = _validate_recurrence(
            schedule_cron=self.schedule_cron,
            timezone_name=self.timezone,
            start_date=self.start_date,
            end_date=self.end_date,
            max_occurrences=self.max_occurrences,
        )
        return self


# Retained name for any existing import; the model itself is now the extended
# one above.
EventTriggerCreate = TriggerCreate


#: The schedule fields a PATCH may touch. Named once so the "you sent a
#: schedule field to an event trigger" check and the merge below cannot drift.
PATCHABLE_SCHEDULE_FIELDS = (
    "schedule_cron", "timezone", "start_date", "end_date", "max_occurrences",
)


class TriggerPatch(BaseModel):
    """Edit a trigger, or pause / resume it.

    SPARSE BY CONSTRUCTION. Only fields the caller actually sent are changed —
    ``model_fields_set`` decides, not "is the value None", because ``None`` is a
    MEANINGFUL value here: ``{"end_date": null}`` clears an end date and
    ``{"max_occurrences": null}`` removes a cap. Reading absence and explicit
    null as the same thing would make an is_active-only pause silently wipe the
    trigger's entire recurrence — exactly the state Task 4 requires pausing to
    preserve.

    ``workflow_definition_id`` and ``trigger_type`` are deliberately NOT
    patchable. Which workflow a trigger starts and how it is triggered are its
    identity; changing either is a delete plus a create, and doing it in place
    would silently re-point a schedule whose occurrence_count and last_fired_at
    describe a different workflow's history.

    ``occurrence_count`` and ``last_fired_at`` are not patchable either — they
    are the firing loop's own record. An endpoint that let a user rewrite
    last_fired_at would hand them a way to re-fire an occurrence the idempotency
    claim has already settled.
    """

    is_active: bool | None = None
    schedule_cron: str | None = None
    timezone: str | None = None
    start_date: datetime | None = None
    end_date: datetime | None = None
    max_occurrences: int | None = None

    @field_validator("start_date", "end_date")
    @classmethod
    def _aware_utc(cls, value: datetime | None) -> datetime | None:
        """A naive bound is read as UTC — same reasoning as TriggerCreate."""
        if value is None or value.tzinfo is not None:
            return value
        return value.replace(tzinfo=_UTC)


#: Upper bound on a preview request. The screen asks for 5; the cap exists so a
#: hand-rolled request cannot ask for 100000 occurrences of a per-minute cron.
PREVIEW_MAX_COUNT = 25
PREVIEW_DEFAULT_COUNT = 5


class SchedulePreview(BaseModel):
    """A dry-run recurrence, evaluated WITHOUT storing anything.

    Same validation as create: a preview of a schedule the create endpoint would
    refuse comes back 422 with the identical message, so the author fixes it
    once rather than discovering the second refusal after pressing Save.
    """

    schedule_cron: str | None = None
    timezone: str = "UTC"
    start_date: datetime | None = None
    end_date: datetime | None = None
    max_occurrences: int | None = None
    #: How many occurrences this trigger has ALREADY had. Sent when previewing
    #: an existing trigger so a cap that is nearly spent previews honestly.
    occurrence_count: int = 0
    #: The instant to preview from. Defaults to now. Sent explicitly by the
    #: verify script so the preview and the scheduler are compared at one fixed
    #: instant rather than at two slightly different "nows".
    after: datetime | None = None
    count: int = PREVIEW_DEFAULT_COUNT

    @field_validator("start_date", "end_date", "after")
    @classmethod
    def _aware_utc(cls, value: datetime | None) -> datetime | None:
        if value is None or value.tzinfo is not None:
            return value
        return value.replace(tzinfo=_UTC)

    @model_validator(mode="after")
    def _coherent(self):
        if self.count < 1 or self.count > PREVIEW_MAX_COUNT:
            raise ValueError(
                f"count must be between 1 and {PREVIEW_MAX_COUNT}"
            )
        if self.occurrence_count < 0:
            raise ValueError("occurrence_count must not be negative")
        self.schedule_cron, self.timezone = _validate_recurrence(
            schedule_cron=self.schedule_cron,
            timezone_name=self.timezone,
            start_date=self.start_date,
            end_date=self.end_date,
            max_occurrences=self.max_occurrences,
        )
        return self


# --------------------------------------------------------------------------
# Granular permission keys (Phase 5) — rows in the global ``permissions``
# catalog seeded by docs/workflowmgr5_part1.sql. Grantable via the SOC
# Profiles / Permission-Sets admin UI; enforced by _require_workflow_permission.
# --------------------------------------------------------------------------
PERM_AUTHOR = "author_workflows"                 # library + editor + save + versions
PERM_VIEW_RUNS = "view_workflow_runs"            # run console + run drill-in
PERM_CONFIGURE_TRIGGERS = "configure_workflow_triggers"  # scheduler / triggers


# --------------------------------------------------------------------------
# Auth helper — GRANULAR permission gate (Phase 5).
#
# Super Admin (platform staff) always passes; everyone else — including an Org
# Admin — must hold ``permission_key`` via their Profile or an assigned
# Permission Set (services.profiles.user_has_permission). This replaces the
# blanket can_manage_org_settings gate so workflow authoring, run-console
# viewing and scheduler configuration are separately grantable, not bundled.
#
# Returns the resolved principal too, so the read consoles can widen a Super
# Admin to all orgs (Org Admin stays scoped to their own org). ``org_id`` comes
# from the authenticated context, never a request body.
# --------------------------------------------------------------------------
async def _require_workflow_permission(
    request: Request, permission_key: str
) -> tuple[str, str, dict]:
    org_id = get_org_id(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        actor_id = await ensure_user(conn, request)
        principal = await load_principal(conn, actor_id)
    if principal is None:
        principal = {"id": actor_id, "org_id": org_id, "role": None}
    if not is_super_admin(principal):
        if not await user_has_permission(pool, actor_id, permission_key):
            raise HTTPException(
                status_code=403, detail=f"Permission required: {permission_key}"
            )
    return actor_id, org_id, principal


# --------------------------------------------------------------------------
# The TRIGGER surface's read gate — WIDER than its write gate (schedulerux).
#
# Up to this sprint ``configure_workflow_triggers`` gated the trigger list AND
# every write on it. That made "a view-only user" unrepresentable: a caller
# without the key got a 403 from the list endpoint and saw nothing at all, so
# there was no read surface left to hide write controls on.
#
# The catalog holds exactly three workflow keys — author_workflows,
# view_workflow_runs, configure_workflow_triggers — so the read is widened to
# the existing operational-read key rather than by inventing a fourth:
#
#     READ   view_workflow_runs  OR  configure_workflow_triggers
#     WRITE  configure_workflow_triggers                (unchanged)
#
# Someone who may watch runs may see what is scheduled to start them; changing
# what is scheduled still needs the configure key. Nobody LOSES access:
# every caller who could read the list before holds the write key and so still
# passes the read gate.
# --------------------------------------------------------------------------
TRIGGER_READ_PERMISSIONS = (PERM_CONFIGURE_TRIGGERS, PERM_VIEW_RUNS)


async def _require_trigger_read(request: Request) -> tuple[str, str, dict, bool]:
    """Gate the trigger READ surface, and resolve write capability in one pass.

    Returns ``(actor_id, org_id, principal, can_write)``. ``can_write`` is
    resolved HERE rather than by a second lookup in each handler so the envelope
    the UI renders from and the gate the writes enforce read the same value from
    the same query.
    """
    org_id = get_org_id(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        actor_id = await ensure_user(conn, request)
        principal = await load_principal(conn, actor_id)
    if principal is None:
        principal = {"id": actor_id, "org_id": org_id, "role": None}

    if is_super_admin(principal):
        return actor_id, org_id, principal, True

    can_write = await user_has_permission(pool, actor_id, PERM_CONFIGURE_TRIGGERS)
    if can_write:
        return actor_id, org_id, principal, True
    if await user_has_permission(pool, actor_id, PERM_VIEW_RUNS):
        return actor_id, org_id, principal, False
    raise HTTPException(
        status_code=403,
        detail=f"Permission required: {PERM_CONFIGURE_TRIGGERS}",
    )


def _trigger_permissions(principal: dict, can_write: bool) -> dict:
    """What this caller may do on the trigger surface, shipped with the page.

    THE UI RENDERS A CREATE / EDIT / PAUSE / DELETE CONTROL ONLY WHEN
    ``can_write`` SAYS SO, and keeps no permission logic of its own — the same
    contract the Portfolio UX screens use. It is not the enforcement: every
    write endpoint re-checks server-side, and verify_schedulerux asserts the two
    independently, because a hidden control over an open endpoint and a gated
    endpoint under a visible button are both real bugs and neither is ruled out
    by testing the other.
    """
    return {
        "can_read": True,          # this envelope is only built after the gate
        "can_write": bool(can_write),
        "is_super_admin": bool(is_super_admin(principal)),
        "read_permission": PERM_VIEW_RUNS,
        "write_permission": PERM_CONFIGURE_TRIGGERS,
    }


def _jsonb(value):
    """asyncpg hands jsonb back as a text string (no json codec is registered);
    decode it so it serializes as nested JSON rather than an escaped string."""
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (ValueError, TypeError):
            return value
    return value


def _action_options() -> list[ActionOption]:
    return [
        ActionOption(key=a.key, access_type=a.access_type, description=a.description)
        for a in REGISTRY.all()
    ]


# --------------------------------------------------------------------------
# Library
# --------------------------------------------------------------------------
@router.get("/admin/workflows", response_model=list[WorkflowSummary])
async def list_workflows(request: Request):
    """Every workflow definition for the caller's org, with a current-version
    step/tier summary. Scoped to ``org_id`` (never returns another org's)."""
    _, org_id, _ = await _require_workflow_permission(request, PERM_AUTHOR)
    pool = await get_pool()
    async with pool.acquire() as conn:
        defs = await conn.fetch(
            """
            SELECT d.id, d.name, d.description,
                   v.version_number AS current_version_number
            FROM workflow_definitions d
            LEFT JOIN workflow_versions v
                ON v.workflow_definition_id = d.id
                AND v.org_id = d.org_id
                AND v.is_current = true
            WHERE d.org_id = $1
            ORDER BY d.name
            """,
            org_id,
        )
        summaries = await conn.fetch(
            """
            SELECT v.workflow_definition_id AS def_id,
                   count(s.id) AS step_count,
                   count(*) FILTER (WHERE s.autonomy_tier <= 2) AS approval_step_count
            FROM workflow_versions v
            JOIN workflow_steps s
                ON s.workflow_version_id = v.id AND s.org_id = v.org_id
            WHERE v.org_id = $1 AND v.is_current = true
            GROUP BY v.workflow_definition_id
            """,
            org_id,
        )
    counts = {str(r["def_id"]): r for r in summaries}
    result = []
    for d in defs:
        c = counts.get(str(d["id"]))
        result.append(
            WorkflowSummary(
                id=d["id"],
                name=d["name"],
                description=d["description"],
                current_version_number=d["current_version_number"],
                step_count=int(c["step_count"]) if c else 0,
                approval_step_count=int(c["approval_step_count"]) if c else 0,
            )
        )
    return result


@router.post("/admin/workflows", response_model=WorkflowSummary, status_code=201)
async def create_workflow(request: Request, body: WorkflowCreate):
    """Create a brand-new definition from a natural-language description via
    Phase 2's validated generator. ``org_id`` + ``created_by`` come from the
    authenticated context."""
    actor_id, org_id, _ = await _require_workflow_permission(request, PERM_AUTHOR)
    if not body.description or not body.description.strip():
        raise HTTPException(status_code=422, detail="A description is required")
    pool = await get_pool()
    try:
        result = await generate_workflow(
            pool,
            org_id=org_id,
            description=body.description,
            created_by=actor_id,
            name=(body.name.strip() if body.name and body.name.strip() else None),
        )
    except WorkflowGenerationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    steps = result.get("steps", [])
    async with pool.acquire() as conn:
        name = await conn.fetchval(
            "SELECT name FROM workflow_definitions WHERE id = $1 AND org_id = $2",
            result["workflow_definition_id"], org_id,
        )
    return WorkflowSummary(
        id=result["workflow_definition_id"],
        name=name or (body.name or "Untitled workflow"),
        description=body.description,
        current_version_number=1,
        step_count=len(steps),
        approval_step_count=len([s for s in steps if s.get("autonomy_tier", 1) <= 2]),
    )


# --------------------------------------------------------------------------
# Diagram editor
# --------------------------------------------------------------------------
@router.get("/admin/workflows/{definition_id}", response_model=WorkflowDetail)
async def get_workflow(request: Request, definition_id: UUID):
    """Everything the diagram editor needs for one definition: current BPMN,
    derived steps, and the closed reference lists for the properties pickers."""
    _, org_id, _ = await _require_workflow_permission(request, PERM_AUTHOR)
    pool = await get_pool()
    async with pool.acquire() as conn:
        definition = await conn.fetchrow(
            "SELECT id, name, description FROM workflow_definitions "
            "WHERE id = $1 AND org_id = $2",
            definition_id, org_id,
        )
        if definition is None:
            raise HTTPException(status_code=404, detail="Workflow not found")
        version = await conn.fetchrow(
            """
            SELECT id, version_number, bpmn_xml
            FROM workflow_versions
            WHERE workflow_definition_id = $1 AND org_id = $2 AND is_current = true
            """,
            definition_id, org_id,
        )
        if version is None:
            raise HTTPException(
                status_code=409, detail="Workflow has no current version"
            )
        step_rows = await conn.fetch(
            """
            SELECT step_key, step_type, autonomy_tier, action_registry_key,
                   assigned_role_profile_id, display_name
            FROM workflow_steps
            WHERE workflow_version_id = $1 AND org_id = $2
            ORDER BY step_key
            """,
            version["id"], org_id,
        )
        profile_rows = await conn.fetch(
            "SELECT id, name FROM profiles WHERE org_id = $1 ORDER BY name",
            org_id,
        )

    return WorkflowDetail(
        id=definition["id"],
        name=definition["name"],
        description=definition["description"],
        current_version=CurrentVersion(
            id=version["id"],
            version_number=version["version_number"],
            bpmn_xml=version["bpmn_xml"],
        ),
        steps=[WorkflowStep(**dict(r)) for r in step_rows],
        profiles=[ProfileOption(id=r["id"], name=r["name"]) for r in profile_rows],
        actions=_action_options(),
    )


@router.post(
    "/admin/workflows/{definition_id}/versions",
    response_model=CurrentVersion,
    status_code=201,
)
async def save_workflow_version(request: Request, definition_id: UUID, body: VersionSave):
    """Save an edited BPMN as a NEW current version and re-derive its steps.

    Never mutates an existing version. Rejects invalid BPMN (bad SpiffWorkflow
    parse OR a reference to a non-existent action key / profile) with a clear
    error, storing nothing."""
    actor_id, org_id, _ = await _require_workflow_permission(request, PERM_AUTHOR)
    pool = await get_pool()
    try:
        result = await save_new_version(
            pool,
            definition_id=definition_id,
            org_id=org_id,
            bpmn_xml=body.bpmn_xml,
            created_by=actor_id,
            change_summary=body.change_summary,
        )
    except WorkflowEditError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except WorkflowValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    async with pool.acquire() as conn:
        xml = await conn.fetchval(
            "SELECT bpmn_xml FROM workflow_versions WHERE id = $1",
            result["workflow_version_id"],
        )
    return CurrentVersion(
        id=result["workflow_version_id"],
        version_number=result["version_number"],
        bpmn_xml=xml,
    )


# --------------------------------------------------------------------------
# Phase 4 — read-only consoles (Run Console / Scheduler / Version History)
#
# Phase 5 gates these granularly: the Run Console + drill-in need
# ``view_workflow_runs``; the Scheduler viewer needs
# ``configure_workflow_triggers``; Version History is part of the authoring
# surface (``author_workflows``). Org Admin sees only their own org's rows;
# Super Admin sees across ALL orgs. org_id is always resolved from the
# authenticated context, never from the request body.
# --------------------------------------------------------------------------
@router.get("/admin/workflow-runs")
async def list_workflow_runs(request: Request):
    """Run Console list: the org's workflow runs (all-orgs for Super Admin)."""
    _, org_id, principal = await _require_workflow_permission(request, PERM_VIEW_RUNS)
    all_orgs = is_super_admin(principal)
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT r.id, r.org_id, r.status, r.started_by, r.started_at,
                   r.completed_at, r.error_detail,
                   d.id AS definition_id, d.name AS workflow_name,
                   v.version_number,
                   u.full_name AS started_by_name, u.email AS started_by_email
            FROM workflow_runs r
            JOIN workflow_versions v ON v.id = r.workflow_version_id
            JOIN workflow_definitions d ON d.id = v.workflow_definition_id
            LEFT JOIN users u ON u.id = r.started_by
            {"" if all_orgs else "WHERE r.org_id = $1"}
            ORDER BY r.started_at DESC
            LIMIT 200
            """,
            *([] if all_orgs else [org_id]),
        )
    return [dict(r) for r in rows]


@router.get("/admin/workflow-runs/{run_id}")
async def get_workflow_run(request: Request, run_id: UUID):
    """Drill into one run: its status plus each run-step's status/result/error."""
    _, org_id, principal = await _require_workflow_permission(request, PERM_VIEW_RUNS)
    all_orgs = is_super_admin(principal)
    pool = await get_pool()
    async with pool.acquire() as conn:
        run = await conn.fetchrow(
            f"""
            SELECT r.id, r.org_id, r.status, r.started_by, r.started_at,
                   r.completed_at, r.error_detail, r.context,
                   d.id AS definition_id, d.name AS workflow_name,
                   v.version_number,
                   u.full_name AS started_by_name, u.email AS started_by_email
            FROM workflow_runs r
            JOIN workflow_versions v ON v.id = r.workflow_version_id
            JOIN workflow_definitions d ON d.id = v.workflow_definition_id
            LEFT JOIN users u ON u.id = r.started_by
            WHERE r.id = $1{"" if all_orgs else " AND r.org_id = $2"}
            """,
            *([run_id] if all_orgs else [run_id, org_id]),
        )
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found")
        step_rows = await conn.fetch(
            """
            SELECT rs.id, rs.status, rs.result, rs.error_detail,
                   rs.started_at, rs.completed_at, rs.proposed_by, rs.approved_by,
                   ws.step_key, ws.step_type, ws.display_name, ws.autonomy_tier
            FROM workflow_run_steps rs
            JOIN workflow_steps ws ON ws.id = rs.workflow_step_id
            WHERE rs.workflow_run_id = $1
            ORDER BY rs.created_at, ws.step_key
            """,
            run_id,
        )
    run_out = dict(run)
    run_out["context"] = _jsonb(run_out.get("context"))
    steps = []
    for r in step_rows:
        d = dict(r)
        d["result"] = _jsonb(d.get("result"))
        steps.append(d)
    return {"run": run_out, "steps": steps}


#: Rows the scheduler tick actually scans. Named once so the row decorator and
#: the summary both key off the same value — 'scheduled', which is what the
#: deployed data and services.workflow_scheduler both use.
SCHEDULED = "scheduled"

_TRIGGER_COLUMNS = """
    t.id, t.org_id, t.workflow_definition_id, t.trigger_type,
    t.schedule_cron, t.event_type, t.is_active, t.created_at, t.created_by,
    t.timezone, t.start_date, t.end_date, t.max_occurrences,
    t.occurrence_count, t.last_fired_at
"""


def _decorate_trigger(row: dict) -> dict:
    """Add the derived, read-only fields the screen renders.

    ``schedule_summary`` and ``next_occurrence`` are computed HERE, server-side,
    from the same ``services.workflow_schedule`` functions the firing loop uses.
    The client renders them; it does not build them. A cron-to-English renderer
    living in the browser would be a second opinion about what a schedule means,
    and the browser's opinion is the one the operator reads while the server's is
    the one that runs.

    A schedule the describer or the recurrence engine chokes on degrades to the
    raw cron string plus ``schedule_error`` — visible and diagnosable, rather
    than a 500 that takes the whole list down because one row is malformed.
    """
    out = dict(row)
    if out.get("trigger_type") != SCHEDULED:
        out["schedule_summary"] = (
            f"On {out['event_type']}" if out.get("event_type") else "—"
        )
        out["next_occurrence"] = None
        out["schedule_error"] = None
        return out

    out["schedule_summary"] = describe_schedule(
        out.get("schedule_cron"), out.get("timezone")
    )
    out["schedule_error"] = None
    out["next_occurrence"] = None
    # A paused trigger has no next occurrence, and saying "next: 9:00 AM
    # tomorrow" next to a Paused pill is the single most misleading thing this
    # screen could print.
    if not out.get("is_active"):
        return out
    try:
        upcoming = next_occurrences(
            schedule_cron=out.get("schedule_cron"),
            timezone_name=out.get("timezone"),
            after_utc=datetime.now(_UTC),
            count=1,
            start_date=out.get("start_date"),
            end_date=out.get("end_date"),
            max_occurrences=out.get("max_occurrences"),
            occurrence_count=out.get("occurrence_count") or 0,
        )
    except ScheduleError as exc:
        out["schedule_error"] = str(exc)
        return out
    out["next_occurrence"] = upcoming[0] if upcoming else None
    return out


@router.get("/admin/workflow-triggers")
async def list_workflow_triggers(request: Request):
    """The Triggers screen: every trigger for the org (all orgs for Super Admin).

    Returns an ENVELOPE — ``{rows, permissions}`` — not a bare list. The
    permissions block is what decides whether the screen renders a create / edit
    / pause / delete control, resolved server-side and shipped with the page,
    exactly as the Portfolio UX screens do. Before this sprint the endpoint
    returned a bare list and the read gate was the write gate, so "a view-only
    caller" did not exist to publish an envelope to.
    """
    _, org_id, principal, can_write = await _require_trigger_read(request)
    all_orgs = is_super_admin(principal)
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT {_TRIGGER_COLUMNS},
                   d.id AS definition_id, d.name AS workflow_name,
                   u.full_name AS created_by_name, u.email AS created_by_email
            FROM workflow_triggers t
            JOIN workflow_definitions d ON d.id = t.workflow_definition_id
            LEFT JOIN users u ON u.id = t.created_by
            {"" if all_orgs else "WHERE t.org_id = $1"}
            ORDER BY d.name, t.trigger_type, t.created_at
            """,
            *([] if all_orgs else [org_id]),
        )
    return {
        "rows": [_decorate_trigger(dict(r)) for r in rows],
        "permissions": _trigger_permissions(principal, can_write),
    }


async def _load_trigger_for_write(request: Request, trigger_id: UUID):
    """Resolve a trigger the caller is allowed to CHANGE, or raise.

    403 when the caller may read the surface but not write it (the message names
    the missing key); 404 when the trigger does not exist OR belongs to another
    org — a 403 there would confirm the existence of another tenant's row.
    """
    actor_id, org_id, principal, can_write = await _require_trigger_read(request)
    if not can_write:
        raise HTTPException(
            status_code=403,
            detail=f"Permission required: {PERM_CONFIGURE_TRIGGERS}",
        )
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            f"SELECT {_TRIGGER_COLUMNS} FROM workflow_triggers t WHERE t.id = $1",
            trigger_id,
        )
    if row is None or (
        not is_super_admin(principal) and str(row["org_id"]) != str(org_id)
    ):
        raise HTTPException(status_code=404, detail="Trigger not found")
    return dict(row), principal, pool, actor_id


# Chancery Phase 7 — the ONLY write on the Scheduler surface was, originally,
# an event trigger (event_type='document_confirmed'). The scheduler sprint adds
# the schedule-type path alongside it. Both are gated by the SAME
# configure_workflow_triggers permission as the viewer. This CONFIGURES which
# runs auto-start; it does not weaken any per-step tier.
EVENT_DOCUMENT_CONFIRMED = "document_confirmed"


@router.post("/admin/workflow-triggers", status_code=201)
async def create_workflow_trigger(request: Request, body: TriggerCreate):
    """Create an event or a scheduled trigger for a definition in the caller's org.

    ``trigger_type='event'`` (the default, and what every pre-existing caller
    sends) still accepts only the one wired event type. ``trigger_type=
    'scheduled'`` creates a row the cron tick will actually fire — the cron
    expression and IANA zone were already validated by the body model, so an
    unrunnable schedule cannot be stored active.
    """
    actor_id, org_id, principal = await _require_workflow_permission(
        request, PERM_CONFIGURE_TRIGGERS
    )
    if body.trigger_type == "event" and body.event_type != EVENT_DOCUMENT_CONFIRMED:
        raise HTTPException(
            status_code=422,
            detail=f"Only the '{EVENT_DOCUMENT_CONFIRMED}' event type is "
                   "supported for event triggers in this phase",
        )
    pool = await get_pool()
    async with pool.acquire() as conn:
        # The definition must exist in the caller's own org — never trust a
        # body-supplied definition to belong to another org. Super Admin may
        # target any org's definition, but the trigger is created in THAT
        # definition's org (org_id follows the definition, not the body).
        definition = await conn.fetchrow(
            "SELECT id, org_id FROM workflow_definitions WHERE id = $1",
            body.workflow_definition_id,
        )
        if definition is None or (
            not is_super_admin(principal)
            and str(definition["org_id"]) != str(org_id)
        ):
            raise HTTPException(status_code=404, detail="Workflow not found")
        row = await conn.fetchrow(
            """
            INSERT INTO workflow_triggers
                (workflow_definition_id, org_id, trigger_type, event_type,
                 schedule_cron, timezone, start_date, end_date, max_occurrences,
                 is_active, created_by)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
            RETURNING id, occurrence_count, last_fired_at
            """,
            body.workflow_definition_id, definition["org_id"], body.trigger_type,
            body.event_type, body.schedule_cron, body.timezone,
            body.start_date, body.end_date, body.max_occurrences,
            body.is_active, actor_id,
        )
    out = {
        "id": str(row["id"]),
        "workflow_definition_id": str(body.workflow_definition_id),
        "org_id": str(definition["org_id"]),
        "trigger_type": body.trigger_type,
        "event_type": body.event_type,
        "is_active": body.is_active,
    }
    if body.trigger_type == "scheduled":
        out.update({
            "schedule_cron": body.schedule_cron,
            "timezone": body.timezone,
            "start_date": body.start_date,
            "end_date": body.end_date,
            "max_occurrences": body.max_occurrences,
            "occurrence_count": row["occurrence_count"],
            "last_fired_at": row["last_fired_at"],
        })
    return out


# --------------------------------------------------------------------------
# The dry-run preview.
#
# Declared BEFORE the /{trigger_id} routes so '/preview' can never be read as a
# trigger id. It is a POST rather than a GET because the thing being previewed
# is a whole recurrence definition — cron, zone, two bounds and a cap — and
# smearing that across a query string invites exactly the encoding bugs the
# 422s exist to prevent. It writes nothing.
# --------------------------------------------------------------------------
@router.post("/admin/workflow-triggers/preview")
async def preview_workflow_schedule(request: Request, body: SchedulePreview):
    """The next N real occurrences of a recurrence, BEFORE anything is saved.

    Computed by ``services.workflow_schedule.next_occurrences`` — the same
    module, the same ``build_recurrence``, the same timezone handling that
    ``evaluate_trigger`` uses inside the firing loop. The recurrence is NOT
    recomputed here; this endpoint validates, calls, and formats.

    That is the whole requirement. A preview with its own copy of the RRULE
    translation would agree with the scheduler today and diverge the first time
    either changed, and the divergence would show up as a workflow running at a
    time the author was never shown. verify_schedulerux proves the equivalence
    by driving ``evaluate_trigger`` at each previewed instant.

    Gated on ``configure_workflow_triggers``: previewing is part of authoring a
    schedule, and a view-only caller has nothing to preview.
    """
    _, _, _ = await _require_workflow_permission(request, PERM_CONFIGURE_TRIGGERS)
    after = body.after or datetime.now(_UTC)
    try:
        occurrences = next_occurrences(
            schedule_cron=body.schedule_cron,
            timezone_name=body.timezone,
            after_utc=after,
            count=body.count,
            start_date=body.start_date,
            end_date=body.end_date,
            max_occurrences=body.max_occurrences,
            occurrence_count=body.occurrence_count,
        )
    except ScheduleError as exc:
        # Defence in depth: the body model already refused an unparseable cron
        # or zone. Anything that still reaches here is a 422, not a 500.
        raise HTTPException(status_code=422, detail=str(exc)) from None

    zone = resolve_timezone(body.timezone)
    return {
        "schedule_cron": body.schedule_cron,
        "timezone": body.timezone,
        "summary": describe_schedule(body.schedule_cron, body.timezone),
        "after": after,
        "requested": body.count,
        "occurrences": [
            {
                "utc": occurrence,
                "local": occurrence.astimezone(zone).isoformat(),
            }
            for occurrence in occurrences
        ],
        # Fewer than requested means a bound really stops it — an end_date or a
        # max_occurrences cap. Said explicitly so the screen can show "this is
        # the last one" instead of a short list the reader has to interpret.
        "exhausted": len(occurrences) < body.count,
    }


@router.patch("/admin/workflow-triggers/{trigger_id}")
async def update_workflow_trigger(
    request: Request, trigger_id: UUID, body: TriggerPatch
):
    """Edit a trigger's recurrence, or pause / resume it.

    PAUSE IS THIS ENDPOINT WITH ``{"is_active": false}`` AND NOTHING ELSE. The
    UPDATE below writes every column from a MERGE of the stored row with only
    the fields the caller actually sent, so a pause cannot clear the cron
    expression, the zone, the bounds, the cap, ``occurrence_count`` or
    ``last_fired_at``. Resuming is the same call with ``true`` and the trigger
    picks up exactly where it was — which is the entire difference between
    pausing and deleting, and the reason both exist.

    Editing RE-VALIDATES the merged recurrence with the same
    ``_validate_recurrence`` create uses, against the merged values rather than
    the submitted ones: sending only ``{"end_date": ...}`` still has to be
    checked against the STORED ``start_date``, or an edit could order the bounds
    backwards one field at a time and store a trigger create would have refused.
    """
    row, principal, pool, _ = await _load_trigger_for_write(request, trigger_id)
    sent = body.model_fields_set

    if "is_active" in sent and body.is_active is None:
        raise HTTPException(
            status_code=422,
            detail="is_active may not be null — send true or false",
        )

    if row["trigger_type"] != SCHEDULED:
        offered = sorted(f for f in PATCHABLE_SCHEDULE_FIELDS if f in sent)
        if offered:
            raise HTTPException(
                status_code=422,
                detail=f"{', '.join(offered)} may only be set when "
                       "trigger_type='scheduled'",
            )

    merged = {
        field: (getattr(body, field) if field in sent else row[field])
        for field in PATCHABLE_SCHEDULE_FIELDS
    }
    is_active = body.is_active if "is_active" in sent else row["is_active"]

    if row["trigger_type"] == SCHEDULED:
        try:
            merged["schedule_cron"], merged["timezone"] = _validate_recurrence(
                schedule_cron=merged["schedule_cron"],
                timezone_name=merged["timezone"],
                start_date=merged["start_date"],
                end_date=merged["end_date"],
                max_occurrences=merged["max_occurrences"],
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from None
        # A cap already reached cannot be re-armed by lowering it; refuse rather
        # than store a trigger that is permanently, invisibly spent.
        cap = merged["max_occurrences"]
        if (cap is not None and is_active
                and (row["occurrence_count"] or 0) >= cap):
            raise HTTPException(
                status_code=422,
                detail=f"max_occurrences ({cap}) is not above the "
                       f"{row['occurrence_count']} occurrence(s) this trigger "
                       "has already fired; it would never fire again",
            )

    async with pool.acquire() as conn:
        updated = await conn.fetchrow(
            f"""
            UPDATE workflow_triggers t
               SET is_active = $2,
                   schedule_cron = $3,
                   timezone = $4,
                   start_date = $5,
                   end_date = $6,
                   max_occurrences = $7
             WHERE t.id = $1
            RETURNING {_TRIGGER_COLUMNS}
            """,
            trigger_id, is_active, merged["schedule_cron"], merged["timezone"],
            merged["start_date"], merged["end_date"], merged["max_occurrences"],
        )
        names = await conn.fetchrow(
            """SELECT d.id AS definition_id, d.name AS workflow_name,
                      u.full_name AS created_by_name, u.email AS created_by_email
               FROM workflow_definitions d
               LEFT JOIN users u ON u.id = $2
               WHERE d.id = $1""",
            updated["workflow_definition_id"], updated["created_by"],
        )
    return _decorate_trigger({**dict(updated), **dict(names or {})})


@router.delete("/admin/workflow-triggers/{trigger_id}")
async def delete_workflow_trigger(request: Request, trigger_id: UUID):
    """Delete a trigger. Irreversible, and deliberately not a soft delete.

    ``is_active = false`` ALREADY MEANS "stop firing but keep everything" — that
    is what pause is. A second, hidden not-really-deleted state would give the
    screen two ways to show a trigger that does not fire and the scheduler two
    ways to skip one, and an operator asking "why did this not run" would have
    to distinguish them. Delete removes the row; the tick's scan cannot see it
    afterwards because there is nothing to see.

    The workflow definition, its versions and every run the trigger ever started
    are untouched — a trigger is a schedule, not the history of what it did.
    """
    row, _, pool, _ = await _load_trigger_for_write(request, trigger_id)
    async with pool.acquire() as conn:
        deleted = await conn.fetchval(
            "DELETE FROM workflow_triggers WHERE id = $1 RETURNING id",
            trigger_id,
        )
    if deleted is None:
        raise HTTPException(status_code=404, detail="Trigger not found")
    return {
        "deleted": True,
        "id": str(deleted),
        "trigger_type": row["trigger_type"],
        "occurrence_count": row["occurrence_count"],
    }


@router.get("/admin/workflows/{definition_id}/versions")
async def list_workflow_versions(request: Request, definition_id: UUID):
    """Version History: every version of a definition in order, exactly one
    marked is_current. Read-only browsing (no diff rendering this phase)."""
    _, org_id, principal = await _require_workflow_permission(request, PERM_AUTHOR)
    all_orgs = is_super_admin(principal)
    pool = await get_pool()
    async with pool.acquire() as conn:
        definition = await conn.fetchrow(
            "SELECT id, org_id, name FROM workflow_definitions WHERE id = $1",
            definition_id,
        )
        # Org Admins may only browse their own org's definitions; a 404 (rather
        # than 403) avoids confirming another org's definition exists.
        if definition is None or (not all_orgs and str(definition["org_id"]) != str(org_id)):
            raise HTTPException(status_code=404, detail="Workflow not found")
        rows = await conn.fetch(
            """
            SELECT v.id, v.version_number, v.change_summary, v.is_current,
                   v.created_at, v.created_by,
                   u.full_name AS created_by_name, u.email AS created_by_email
            FROM workflow_versions v
            LEFT JOIN users u ON u.id = v.created_by
            WHERE v.workflow_definition_id = $1
            ORDER BY v.version_number ASC
            """,
            definition_id,
        )
    return {
        "definition": {"id": definition["id"], "name": definition["name"]},
        "versions": [dict(r) for r in rows],
    }
