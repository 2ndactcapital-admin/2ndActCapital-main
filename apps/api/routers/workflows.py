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

Gate: ``can_manage_org_settings`` — super_admin anywhere, org_admin at home —
identical to the SOC Profiles / Permission-Sets / Restricted-Access screens.
``org_id`` is always resolved server-side from the authenticated context and
every query is scoped to it; it is NEVER read from a request body.
"""

import json
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from routers.entities import get_org_id
from services.action_registry import REGISTRY
from services.database import get_pool
from services.rbac import can_manage_org_settings, is_super_admin, load_principal
from services.users import ensure_user
from services.workflow_editor import (
    WorkflowEditError,
    WorkflowValidationError,
    save_new_version,
)
from services.workflow_nl_generator import WorkflowGenerationError, generate_workflow

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


# --------------------------------------------------------------------------
# Auth helper — Org Admin (own org) or Super Admin (any org).
# Same contract as routers/profiles.py::_require_admin.
# --------------------------------------------------------------------------
async def _require_admin(request: Request) -> tuple[str, str]:
    org_id = get_org_id(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        actor_id = await ensure_user(conn, request)
        principal = await load_principal(conn, actor_id)
    if principal is None:
        principal = {"id": actor_id, "org_id": org_id, "role": None}
    if not can_manage_org_settings(principal, org_id):
        raise HTTPException(status_code=403, detail="Admin access required")
    return actor_id, org_id


async def _require_admin_principal(request: Request) -> tuple[str, str, dict]:
    """Like ``_require_admin`` but also returns the resolved principal so read
    screens can widen to all-orgs for a Super Admin (org_admin stays home)."""
    org_id = get_org_id(request)
    pool = await get_pool()
    async with pool.acquire() as conn:
        actor_id = await ensure_user(conn, request)
        principal = await load_principal(conn, actor_id)
    if principal is None:
        principal = {"id": actor_id, "org_id": org_id, "role": None}
    if not can_manage_org_settings(principal, org_id):
        raise HTTPException(status_code=403, detail="Admin access required")
    return actor_id, org_id, principal


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
    _, org_id = await _require_admin(request)
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
    actor_id, org_id = await _require_admin(request)
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
    _, org_id = await _require_admin(request)
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
    actor_id, org_id = await _require_admin(request)
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
# All gated by the same admin contract. Org Admin sees only their own org's
# rows; Super Admin sees across ALL orgs. org_id is always resolved from the
# authenticated context, never from the request body.
# --------------------------------------------------------------------------
@router.get("/admin/workflow-runs")
async def list_workflow_runs(request: Request):
    """Run Console list: the org's workflow runs (all-orgs for Super Admin)."""
    _, org_id, principal = await _require_admin_principal(request)
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
    _, org_id, principal = await _require_admin_principal(request)
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


@router.get("/admin/workflow-triggers")
async def list_workflow_triggers(request: Request):
    """Scheduler / Routine Viewer: triggers for the org (all-orgs for Super
    Admin). READ/CONFIGURE only in this phase — nothing fires autonomously."""
    _, org_id, principal = await _require_admin_principal(request)
    all_orgs = is_super_admin(principal)
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""
            SELECT t.id, t.org_id, t.trigger_type, t.schedule_cron, t.event_type,
                   t.is_active, t.created_at,
                   d.id AS definition_id, d.name AS workflow_name
            FROM workflow_triggers t
            JOIN workflow_definitions d ON d.id = t.workflow_definition_id
            {"" if all_orgs else "WHERE t.org_id = $1"}
            ORDER BY d.name, t.trigger_type
            """,
            *([] if all_orgs else [org_id]),
        )
    return [dict(r) for r in rows]


@router.get("/admin/workflows/{definition_id}/versions")
async def list_workflow_versions(request: Request, definition_id: UUID):
    """Version History: every version of a definition in order, exactly one
    marked is_current. Read-only browsing (no diff rendering this phase)."""
    _, org_id, principal = await _require_admin_principal(request)
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
