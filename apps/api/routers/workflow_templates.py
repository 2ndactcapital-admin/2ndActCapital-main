"""WFTEMPLATES1 — the workflow template library. services.workflow_templates holds the rules; every route names its
gate (docs/AUTHZ1_ROUTES.json; authz1_routes.py reads it). Endpoint functions carry a ``wt_`` prefix so the route
scanner never confuses one with a service function of the same name.

ORG (/admin/workflow-templates)
  any(author_workflows | view_workflow_runs | configure_workflow_triggers)   — browse (a member holds none)
      GET  /admin/workflow-templates                         the catalog (?category=, ?q=)
      GET  /admin/workflow-templates/copies                  this org's copies + "a newer version exists" + diff
      GET  /admin/workflow-templates/{template_id}           one template (?version_id=): description, diagram,
                                                             steps with tiers, parameters, prerequisites (named
                                                             when missing), this org's copies
  author_workflows
      POST /admin/workflow-templates/{template_id}/use       copy into this org as a NEW draft workflow
      POST /admin/workflow-templates/org                     save one of this org's workflows as an org template
      POST /admin/workflow-templates/versions/{version_id}/retire     retire an ORG template version

PLATFORM (/admin/platform/workflow-templates) — Super Admin, in the platform org (the caller's own org)
      GET  /admin/platform/workflow-templates                templates, versions, usage COUNTS only
      POST /admin/platform/workflow-templates                draft a version (the maker)
      POST /admin/platform/workflow-templates/versions/{version_id}/decide    publish / reject (a DIFFERENT person)
      POST /admin/platform/workflow-templates/versions/{version_id}/retire

Super Admin first (services.authz). org_id is only ever the caller's (get_org_id) — never a body or path value;
every request model forbids extras. Every read publishes the permission envelope (Rule 1): write controls render
only on ``permissions.can_write`` / ``is_super_admin``, and ``vocabularies.editable`` is an empty list for a
view-only caller.
"""
from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from routers.entities import get_org_id
from services import workflow_templates as WT
from services.authz import require_any_permission, require_permission, require_super_admin
from services.database import get_pool

router = APIRouter(tags=["workflow_templates"])

PERM_AUTHOR = "author_workflows"
READ_PERMS = ("author_workflows", "view_workflow_runs", "configure_workflow_triggers")


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class UseIn(_Body):
    version_id: UUID | None = None
    parameters: dict[str, Any] = Field(default_factory=dict)
    name: str | None = Field(default=None, max_length=200)


class OrgSaveIn(_Body):
    workflow_definition_id: UUID
    key: str = Field(min_length=3, max_length=64)
    category: str = Field(min_length=1, max_length=40)
    title: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=8000)


class DraftIn(_Body):
    key: str = Field(min_length=3, max_length=64)
    title: str = Field(min_length=1, max_length=200)
    category: str = Field(min_length=1, max_length=40)
    description: str = Field(min_length=1, max_length=8000)
    bpmn_xml: str = Field(min_length=1, max_length=200000)
    parameters: list[dict[str, Any]] = Field(default_factory=list)


class DecideIn(_Body):
    outcome: Literal["approve", "reject"]
    note: str | None = Field(default=None, max_length=2000)


class RetireIn(_Body):
    reason: str = Field(min_length=1, max_length=2000)


async def _act(coro):
    try:
        return await coro
    except WT.TemplateError as exc:
        raise HTTPException(status_code=422, detail={"message": str(exc), "errors": exc.errors}) from exc
    except WT.TemplateNotFound as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except WT.TemplatePermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except WT.TemplateConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


def _envelope(principal) -> dict:
    can_write = bool(principal.has(PERM_AUTHOR))
    return {
        "permissions": {"can_read": True, "can_write": can_write, "is_super_admin": bool(principal.is_super_admin),
                        "read_permission": "|".join(READ_PERMS), "write_permission": PERM_AUTHOR},
        "vocabularies": WT.vocabularies(["use", "save", "retire"] if can_write else []),
    }


# ── Org ─────────────────────────────────────────────────────────────────────
@router.get("/admin/workflow-templates")
async def wt_catalog(request: Request, category: str | None = Query(default=None, max_length=40),
                     q: str | None = Query(default=None, max_length=200)):
    principal = require_any_permission(request, READ_PERMS)
    pool = await get_pool()
    body = await _act(WT.catalog(pool, org_id=get_org_id(request), caller=principal, category=category, q=q))
    return {**body, **_envelope(principal)}


@router.get("/admin/workflow-templates/copies")
async def wt_copies(request: Request):
    principal = require_any_permission(request, READ_PERMS)
    pool = await get_pool()
    async with pool.acquire() as conn:
        copies = await WT.org_copies(conn, get_org_id(request))
    return {"copies": copies, **_envelope(principal)}


@router.get("/admin/workflow-templates/{template_id}")
async def wt_detail(template_id: UUID, request: Request, version_id: UUID | None = None):
    principal = require_any_permission(request, READ_PERMS)
    pool = await get_pool()
    body = await _act(WT.detail(pool, org_id=get_org_id(request), caller=principal, template_id=template_id,
                                version_id=version_id))
    return {**body, **_envelope(principal)}


@router.post("/admin/workflow-templates/{template_id}/use", status_code=201)
async def wt_use(template_id: UUID, body: UseIn, request: Request):
    principal = require_permission(request, PERM_AUTHOR)
    pool = await get_pool()
    return await _act(WT.use_template(pool, org_id=get_org_id(request), actor_id=principal.user_id,
                                      template_id=template_id, version_id=body.version_id, values=body.parameters,
                                      name=body.name))


@router.post("/admin/workflow-templates/org", status_code=201)
async def wt_save_org(body: OrgSaveIn, request: Request):
    principal = require_permission(request, PERM_AUTHOR)
    pool = await get_pool()
    return await _act(WT.save_org_template(pool, org_id=get_org_id(request), actor_id=principal.user_id,
                                           workflow_definition_id=body.workflow_definition_id, key=body.key,
                                           category=body.category, title=body.title, description=body.description))


@router.post("/admin/workflow-templates/versions/{version_id}/retire")
async def wt_retire_org(version_id: UUID, body: RetireIn, request: Request):
    principal = require_permission(request, PERM_AUTHOR)
    pool = await get_pool()
    return await _act(WT.retire_version(pool, org_id=get_org_id(request), actor_id=principal.user_id,
                                        version_id=version_id, reason=body.reason, platform=False))


# ── Platform ────────────────────────────────────────────────────────────────
@router.get("/admin/platform/workflow-templates")
async def wt_platform(request: Request):
    principal = require_super_admin(request)
    pool = await get_pool()
    body = await _act(WT.platform_overview(pool, org_id=get_org_id(request), actor_id=principal.user_id))
    return {**body, "permissions": {"can_read": True, "can_write": True, "is_super_admin": True,
                                    "read_permission": "super_admin", "write_permission": "super_admin"},
            "vocabularies": WT.vocabularies([], ["draft", "decide", "retire"] if body["platform_org_allowed"] else [])}


@router.post("/admin/platform/workflow-templates", status_code=201)
async def wt_platform_draft(body: DraftIn, request: Request):
    principal = require_super_admin(request)
    pool = await get_pool()
    return await _act(WT.draft_platform_template(pool, org_id=get_org_id(request), actor_id=principal.user_id,
                                                 key=body.key, title=body.title, category=body.category,
                                                 description=body.description, bpmn_xml=body.bpmn_xml,
                                                 parameters=body.parameters))


@router.post("/admin/platform/workflow-templates/versions/{version_id}/decide")
async def wt_platform_decide(version_id: UUID, body: DecideIn, request: Request):
    principal = require_super_admin(request)
    pool = await get_pool()
    return await _act(WT.decide_platform_version(pool, org_id=get_org_id(request), actor_id=principal.user_id,
                                                 version_id=version_id, outcome=body.outcome, note=body.note))


@router.post("/admin/platform/workflow-templates/versions/{version_id}/retire")
async def wt_platform_retire(version_id: UUID, body: RetireIn, request: Request):
    principal = require_super_admin(request)
    pool = await get_pool()
    return await _act(WT.retire_version(pool, org_id=get_org_id(request), actor_id=principal.user_id,
                                        version_id=version_id, reason=body.reason, platform=True))
