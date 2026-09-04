"""UDF definitions layer's first HTTP surface — udf01a Task 3a.

Everything in ``services.portfolio_udf``/``services.portfolio_udf_tags`` had
NO endpoint before this sprint (confirmed by grep in Task 1 discovery). This
router is thin by design: every real decision — widening-only type changes,
tag mint gating, dry-run narrowing checks — lives in the service layer and is
unit-testable without an HTTP client. The router's only job is auth, org_id
resolution, and the permission envelope.

PERMISSION MODEL
──────────────────────────────────────────────────────────────────────────────
No new tenant-facing permission strings beyond the one the sprint explicitly
approved (``create_tags`` — see ``services.portfolio_udf_tags`` for why). Every
other gate reuses ``manage_portfolio``/``view_portfolio``, the existing action
registry — a UDF is portfolio-adjacent custom-field metadata, not a new
resource. Platform-scope writes are ``is_super_admin`` ONLY, checked via the
same ``rbac.is_super_admin`` every other platform-write path uses — never a
second local reimplementation.

``org_id`` is resolved exactly once per request, from ``routers.entities.
get_org_id`` (JWT claims), and is NEVER accepted from the request body — every
request model below either has no org_id field or would fail Pydantic's
``extra='forbid'`` if a caller tried to smuggle one in.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, field_validator

from routers.entities import get_org_id
from services.database import get_pool
from services.permissions import get_user_id
from services.portfolio_udf import (
    APPLIES_TO,
    DATA_TYPES,
    UdfDuplicateError,
    UdfError,
    UdfImmutableError,
    UdfReferencedError,
    UdfScopeError,
    UdfTargetMismatchError,
    UdfTypeChangeError,
    UdfTypeParamError,
    UdfValueTypeError,
    create_org_definition,
    create_platform_definition,
    create_team_definition,
    create_user_definition,
    deactivate_definition,
    get_definition,
    get_udf_value,
    get_value_history,
    list_udf_values_for_target,
    reactivate_definition,
    record_udf_value,
    resolve_visible_definitions,
    soft_delete_definition,
    undelete_definition,
    update_definition,
)
from services.portfolio_udf_field_permissions import (
    ACCESS_EDIT,
    ACCESS_HIDDEN,
    FieldAccessDeniedError,
    list_field_grants,
    resolve_field_access,
    resolve_field_access_bulk,
    set_field_access,
)
from services.portfolio_udf_layouts import (
    LayoutCapError,
    LayoutColSpanError,
    LayoutDuplicateError,
    LayoutError,
    LayoutScopeError,
    LayoutSectionReferencedError,
    add_item,
    add_section,
    get_or_create_layout,
    get_resolved_layout,
    move_item,
    remove_item,
    remove_section,
    reorder_sections,
)
from services.portfolio_udf_tabs import (
    TabCapError,
    TabDuplicateError,
    TabError,
    TabGranteeError,
    TabImmutableError,
    TabReferencedError,
    create_tab,
    deactivate_tab,
    get_tab,
    list_visible_tabs,
    reactivate_tab,
    resolve_tab_visibility,
    set_tab_visibility,
    soft_delete_tab,
    undelete_tab,
    update_tab,
)
from services.portfolio_udf_tags import (
    TAG_CREATE_PERMISSION,
    TagCapError,
    TagPermissionError,
    assign_tags,
    get_vocabulary,
    merge_tags,
)
from services.rbac import has_permission, is_super_admin, load_principal, require_permission

router = APIRouter(tags=["udf"])

READ_PERMISSION = "view_portfolio"
WRITE_PERMISSION = "manage_portfolio"

#: Errors from the service layer that mean "the caller's input was rejected
#: for a reason they can fix" — mapped to 422, never 500. UdfPermissionError
#: and TagPermissionError are handled separately as 403; UdfReferencedError
#: carries structured data the client needs and is handled separately as 409.
_VALIDATION_ERRORS = (
    UdfError, UdfDuplicateError, UdfImmutableError, UdfScopeError,
    UdfTargetMismatchError, UdfTypeChangeError, UdfTypeParamError,
    UdfValueTypeError, TagCapError,
    TabError, TabDuplicateError, TabImmutableError, TabCapError, TabGranteeError,
    LayoutError, LayoutScopeError, LayoutDuplicateError, LayoutCapError,
    LayoutColSpanError,
)


async def _tenant_gate(request: Request, permission: str) -> tuple[str, str, Any]:
    """Resolve ``(org_id, user_id, pool)`` and enforce a TENANT permission.

    Identical shape to ``portfolio_positions._tenant_gate`` — same pool, same
    ``require_permission`` (super-admin bypass checked first, inside
    ``rbac.has_permission``), same 403-naming-the-permission behaviour. Not
    re-implemented differently here on purpose.
    """
    org_id = get_org_id(request)
    pool = await get_pool()
    user_id = get_user_id(request)
    await require_permission(pool, user_id, org_id, permission)
    return org_id, user_id, pool


async def _permission_envelope(pool, user_id: str, org_id: str) -> dict[str, Any]:
    can_write = await has_permission(pool, user_id, org_id, WRITE_PERMISSION)
    can_create_tags = await has_permission(pool, user_id, org_id, TAG_CREATE_PERMISSION)
    async with pool.acquire() as conn:
        principal = await load_principal(conn, user_id)
    super_admin = is_super_admin(principal)
    return {
        "can_read": True,
        "can_write": bool(can_write),
        "can_create_tags": bool(can_create_tags),
        "is_super_admin": bool(super_admin),
        "read_permission": READ_PERMISSION,
        "write_permission": WRITE_PERMISSION,
        "tag_create_permission": TAG_CREATE_PERMISSION,
    }


def _vocabularies(perms: dict[str, Any]) -> dict[str, Any]:
    return {
        "applies_to": sorted(APPLIES_TO),
        "data_type": sorted(DATA_TYPES),
        "owner_scope": ["platform", "org", "team", "user"],
        # Neither list is meaningful to a caller without the write grant —
        # empty, never omitted, never a client-side default. Rule 1 / the
        # Permission Envelope Pattern.
        "editable": ["label", "help_text", "description", "is_required",
                     "default_value", "is_unique", "unique_case_sensitive",
                     "is_external_id", "display_order", "options",
                     "data_type", "type_params"] if perms["can_write"] else [],
        "inline_editable": ["label", "display_order"] if perms["can_write"] else [],
    }


def _tab_vocabularies(perms: dict[str, Any]) -> dict[str, Any]:
    return {
        "applies_to": sorted(APPLIES_TO),
        "editable": ["label"] if perms["can_write"] else [],
        "inline_editable": ["label"] if perms["can_write"] else [],
    }


def _layout_vocabularies(perms: dict[str, Any]) -> dict[str, Any]:
    """Tenant-level write vocabulary only — mirrors ``can_write``, with no
    per-field narrowing. ``get_resolved_layout`` is where FLS actually
    narrows a specific field's ``is_read_only``/presence (Sprint udf01c);
    this vocabulary describes what the LAYOUT mechanics (title, ordering,
    column placement) allow, not any one field's access level, which is
    frontend consumption explicitly held out of this sprint's scope."""
    return {
        "editable": ["title", "display_order", "column_index", "col_span"]
        if perms["can_write"] else [],
        "inline_editable": [],
    }


def _http_error(exc: Exception) -> HTTPException:
    if isinstance(exc, (UdfReferencedError, TabReferencedError, LayoutSectionReferencedError)):
        return HTTPException(
            status_code=409,
            detail={"message": str(exc), "references": exc.references},
        )
    if isinstance(exc, UdfTypeChangeError) and exc.affected_rows is not None:
        return HTTPException(
            status_code=422,
            detail={"message": str(exc), "affected_rows": exc.affected_rows},
        )
    return HTTPException(status_code=422, detail=str(exc))


# ── Request models ───────────────────────────────────────────────────────────


class DefinitionCreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    owner_scope: Literal["platform", "org", "team", "user"]
    owner_scope_id: str | None = None  # team_id or user_id; ignored for platform/org
    applies_to: str
    field_key: str
    label: str
    data_type: str
    options: Any = None
    display_order: int = 0
    type_params: dict[str, Any] | None = None
    api_name: str | None = None
    help_text: str | None = None
    description: str | None = None
    is_required: bool = False
    default_value: Any = None
    is_unique: bool = False
    unique_case_sensitive: bool = False
    is_external_id: bool = False
    value_set_id: str | None = None


class DefinitionUpdateIn(BaseModel):
    """Every field is optional; only keys the caller actually SET are applied
    (``model_fields_set``, read by the handler below) — the sparse-PATCH
    convention this codebase already uses on the Workflow triggers CRUD
    screen."""

    model_config = ConfigDict(extra="forbid")

    label: str | None = None
    help_text: str | None = None
    description: str | None = None
    is_required: bool | None = None
    default_value: Any = None
    is_unique: bool | None = None
    unique_case_sensitive: bool | None = None
    is_external_id: bool | None = None
    display_order: int | None = None
    options: Any = None
    data_type: str | None = None
    type_params: dict[str, Any] | None = None
    api_name: str | None = None


class ValuePutIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    definition_id: str
    value: Any


class TagsPutIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    codes: list[str]

    @field_validator("codes")
    @classmethod
    def _non_empty(cls, v):
        if not v:
            raise ValueError("codes must be a non-empty list")
        return v


class TagMergeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    from_code: str
    into_code: str


class TabCreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    applies_to: str
    label: str
    api_name: str
    display_order: int = 0


class TabUpdateIn(BaseModel):
    """Sparse PATCH — only ``label`` is actually mutable (``api_name`` present
    with a different value is refused by the service layer with a 422)."""

    model_config = ConfigDict(extra="forbid")

    label: str | None = None
    api_name: str | None = None


class TabPermissionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile_id: str | None = None
    permission_set_id: str | None = None
    is_visible: bool


class FieldPermissionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile_id: str | None = None
    permission_set_id: str | None = None
    access: Literal["hidden", "read", "edit"]


class SectionCreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str | None = None
    column_count: int = 2
    display_order: int = 0
    record_type_id: str | None = None


class ItemCreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    definition_id: str | None = None
    display_order: int = 0
    column_index: int = 0
    col_span: int = 1
    is_read_only: bool = False


class ItemUpdateIn(BaseModel):
    """Sparse PATCH for ``move_item`` — section/column/order only.
    ``definition_id`` is not settable here; remove and re-add the item to
    change which field it binds to."""

    model_config = ConfigDict(extra="forbid")

    section_id: str | None = None
    column_index: int | None = None
    col_span: int | None = None
    display_order: int | None = None


# ── Definitions ──────────────────────────────────────────────────────────────


@router.get("/udf/definitions")
async def list_definitions(
    request: Request,
    target_type: str = Query(...),
    scope: Literal["visible"] = Query(default="visible"),
):
    """Every definition this user can see for ``target_type`` — the union of
    all four namespaces, per ``resolve_visible_definitions``. ``scope`` is
    accepted as a query param for forward compatibility with a future
    per-namespace filter; only ``visible`` (the default) is implemented."""
    org_id, user_id, pool = await _tenant_gate(request, READ_PERMISSION)
    async with pool.acquire() as conn:
        try:
            rows = await resolve_visible_definitions(
                conn, org_id=org_id, user_id=user_id, applies_to=target_type
            )
            # No tab is in scope for this endpoint — see the FLS module's
            # docstring for why that means the tab-hidden precedence step is
            # skipped and only the field-grant paths are evaluated.
            access_map = await resolve_field_access_bulk(
                conn, definition_ids=[r["id"] for r in rows], tab_id=None,
                org_id=org_id, user_id=user_id,
            )
            rows = [r for r in rows if access_map.get(r["id"], ACCESS_EDIT) != ACCESS_HIDDEN]
        except _VALIDATION_ERRORS as exc:
            raise _http_error(exc) from exc
    perms = await _permission_envelope(pool, user_id, org_id)
    return {
        "rows": rows,
        "permissions": perms,
        "vocabularies": _vocabularies(perms),
    }


@router.post("/udf/definitions", status_code=201)
async def create_definition(request: Request, body: DefinitionCreateIn):
    org_id, user_id, pool = await _tenant_gate(request, WRITE_PERMISSION)
    async with pool.acquire() as conn:
        principal = await load_principal(conn, user_id)
        super_admin = is_super_admin(principal)
        kwargs = dict(
            applies_to=body.applies_to, field_key=body.field_key, label=body.label,
            data_type=body.data_type, options=body.options,
            display_order=body.display_order, type_params=body.type_params,
            api_name=body.api_name, help_text=body.help_text,
            description=body.description, is_required=body.is_required,
            default_value=body.default_value, is_unique=body.is_unique,
            unique_case_sensitive=body.unique_case_sensitive,
            is_external_id=body.is_external_id, value_set_id=body.value_set_id,
            created_by=user_id,
        )
        try:
            if body.owner_scope == "platform":
                def_id = await create_platform_definition(
                    conn, is_super_admin=super_admin, **kwargs
                )
            elif body.owner_scope == "org":
                def_id = await create_org_definition(conn, org_id=org_id, **kwargs)
            elif body.owner_scope == "team":
                if not body.owner_scope_id:
                    raise UdfError("owner_scope_id (team_id) is required for scope='team'")
                def_id = await create_team_definition(
                    conn, org_id=org_id, team_id=body.owner_scope_id, **kwargs
                )
            else:
                if not body.owner_scope_id:
                    raise UdfError("owner_scope_id (user_id) is required for scope='user'")
                def_id = await create_user_definition(
                    conn, org_id=org_id, user_id=body.owner_scope_id, **kwargs
                )
        except _VALIDATION_ERRORS as exc:
            raise _http_error(exc) from exc
        row = await get_definition(conn, definition_id=def_id)
    return {"id": def_id, "definition": row}


@router.patch("/udf/definitions/{definition_id}")
async def patch_definition(request: Request, definition_id: str, body: DefinitionUpdateIn):
    org_id, user_id, pool = await _tenant_gate(request, WRITE_PERMISSION)
    changes = body.model_dump(exclude_unset=True)
    async with pool.acquire() as conn:
        principal = await load_principal(conn, user_id)
        super_admin = is_super_admin(principal)
        try:
            row = await update_definition(
                conn, definition_id=definition_id, org_id=org_id, changed_by=user_id,
                changes=changes, is_super_admin=super_admin,
            )
        except _VALIDATION_ERRORS as exc:
            raise _http_error(exc) from exc
    return {"definition": row}


@router.delete("/udf/definitions/{definition_id}")
async def delete_definition(request: Request, definition_id: str):
    """Soft delete. 409 with the reference list if anything still points at
    this definition — see ``UdfReferencedError``."""
    org_id, user_id, pool = await _tenant_gate(request, WRITE_PERMISSION)
    async with pool.acquire() as conn:
        principal = await load_principal(conn, user_id)
        super_admin = is_super_admin(principal)
        try:
            row = await soft_delete_definition(
                conn, definition_id=definition_id, org_id=org_id, changed_by=user_id,
                is_super_admin=super_admin,
            )
        except _VALIDATION_ERRORS as exc:
            raise _http_error(exc) from exc
    return {"definition": row}


@router.post("/udf/definitions/{definition_id}/deactivate")
async def deactivate_definition_route(request: Request, definition_id: str):
    org_id, user_id, pool = await _tenant_gate(request, WRITE_PERMISSION)
    async with pool.acquire() as conn:
        principal = await load_principal(conn, user_id)
        super_admin = is_super_admin(principal)
        try:
            row = await deactivate_definition(
                conn, definition_id=definition_id, org_id=org_id, changed_by=user_id,
                is_super_admin=super_admin,
            )
        except _VALIDATION_ERRORS as exc:
            raise _http_error(exc) from exc
    return {"definition": row}


@router.post("/udf/definitions/{definition_id}/reactivate")
async def reactivate_definition_route(request: Request, definition_id: str):
    org_id, user_id, pool = await _tenant_gate(request, WRITE_PERMISSION)
    async with pool.acquire() as conn:
        principal = await load_principal(conn, user_id)
        super_admin = is_super_admin(principal)
        try:
            row = await reactivate_definition(
                conn, definition_id=definition_id, org_id=org_id, changed_by=user_id,
                is_super_admin=super_admin,
            )
        except _VALIDATION_ERRORS as exc:
            raise _http_error(exc) from exc
    return {"definition": row}


@router.post("/udf/definitions/{definition_id}/undelete")
async def undelete_definition_route(request: Request, definition_id: str):
    """Not in the sprint's endpoint list verbatim, but Task 2c requires soft
    delete to be reversible and there is no way to invoke that reversal
    without a route — see the module docstring's scope note."""
    org_id, user_id, pool = await _tenant_gate(request, WRITE_PERMISSION)
    async with pool.acquire() as conn:
        principal = await load_principal(conn, user_id)
        super_admin = is_super_admin(principal)
        try:
            row = await undelete_definition(
                conn, definition_id=definition_id, org_id=org_id, changed_by=user_id,
                is_super_admin=super_admin,
            )
        except _VALIDATION_ERRORS as exc:
            raise _http_error(exc) from exc
    return {"definition": row}


# ── Values ───────────────────────────────────────────────────────────────────


@router.get("/udf/values/{target_type}/{target_id}")
async def get_values(request: Request, target_type: str, target_id: str):
    org_id, user_id, pool = await _tenant_gate(request, READ_PERMISSION)
    async with pool.acquire() as conn:
        try:
            rows = await list_udf_values_for_target(
                conn, org_id=org_id, user_id=user_id,
                target_type=target_type, target_id=target_id,
            )
            # No tab is in scope for this endpoint either — see the FLS
            # module's docstring.
            access_map = await resolve_field_access_bulk(
                conn, definition_ids=[r["definition_id"] for r in rows], tab_id=None,
                org_id=org_id, user_id=user_id,
            )
            rows = [
                r for r in rows
                if access_map.get(r["definition_id"], ACCESS_EDIT) != ACCESS_HIDDEN
            ]
        except _VALIDATION_ERRORS as exc:
            raise _http_error(exc) from exc
    perms = await _permission_envelope(pool, user_id, org_id)
    return {"rows": rows, "permissions": perms}


@router.put("/udf/values/{target_type}/{target_id}")
async def put_value(request: Request, target_type: str, target_id: str, body: ValuePutIn):
    org_id, user_id, pool = await _tenant_gate(request, WRITE_PERMISSION)
    async with pool.acquire() as conn:
        try:
            access = await resolve_field_access(
                conn, definition_id=body.definition_id, tab_id=None,
                org_id=org_id, user_id=user_id,
            )
            if access != ACCESS_EDIT:
                definition = await get_definition(conn, definition_id=body.definition_id)
                field_name = definition["field_key"] if definition else body.definition_id
                raise FieldAccessDeniedError(
                    f"field {field_name!r} (definition_id={body.definition_id}) "
                    f"has access={access!r} for this caller — writes require "
                    f"'edit'"
                )
            value_id = await record_udf_value(
                conn, org_id=org_id, definition_id=body.definition_id,
                target_type=target_type, target_id=target_id, value=body.value,
            )
            current = await get_udf_value(
                conn, org_id=org_id, definition_id=body.definition_id,
                target_type=target_type, target_id=target_id,
            )
            history = await get_value_history(
                conn, org_id=org_id, definition_id=body.definition_id,
                target_type=target_type, target_id=target_id,
            )
        except FieldAccessDeniedError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except _VALIDATION_ERRORS as exc:
            raise _http_error(exc) from exc
    return {"id": value_id, "value": current, "history": history}


# ── Tags ─────────────────────────────────────────────────────────────────────


@router.get("/udf/tags/{definition_id}")
async def get_tag_vocabulary(request: Request, definition_id: str):
    org_id, user_id, pool = await _tenant_gate(request, READ_PERMISSION)
    async with pool.acquire() as conn:
        try:
            rows = await get_vocabulary(conn, definition_id=definition_id)
        except _VALIDATION_ERRORS as exc:
            raise _http_error(exc) from exc
    perms = await _permission_envelope(pool, user_id, org_id)
    return {"rows": rows, "permissions": perms}


@router.put("/udf/tags/{definition_id}/{target_id}")
async def put_tags(request: Request, definition_id: str, target_id: str, body: TagsPutIn):
    """Sets a target's tags to exactly ``codes``. Minting a code not already in
    the vocabulary requires ``create_tags``; assigning an existing one does
    not — see ``services.portfolio_udf_tags`` for the reasoning."""
    org_id, user_id, pool = await _tenant_gate(request, WRITE_PERMISSION)
    async with pool.acquire() as conn:
        can_create_tags = await has_permission(pool, user_id, org_id, TAG_CREATE_PERMISSION)
        try:
            rows = await assign_tags(
                conn, org_id=org_id, definition_id=definition_id, target_id=target_id,
                codes=body.codes, assigned_by=user_id, can_create_tags=can_create_tags,
            )
        except TagPermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except _VALIDATION_ERRORS as exc:
            raise _http_error(exc) from exc
    return {"rows": rows}


@router.post("/udf/tags/{definition_id}/merge")
async def merge_tags_route(request: Request, definition_id: str, body: TagMergeIn):
    org_id, user_id, pool = await _tenant_gate(request, WRITE_PERMISSION)
    async with pool.acquire() as conn:
        try:
            n = await merge_tags(
                conn, org_id=org_id, definition_id=definition_id,
                from_code=body.from_code, into_code=body.into_code, changed_by=user_id,
            )
        except _VALIDATION_ERRORS as exc:
            raise _http_error(exc) from exc
    return {"targets_repointed": n}


# ── Tabs — udf01b Task 3a ───────────────────────────────────────────────────
#
# Tabs are org-scoped only (no platform/team/user namespace — see
# services.portfolio_udf_tabs's module docstring). Tab visibility is a THIRD,
# separate permission system (public.profiles / public.permission_sets), not
# services.rbac's roles — see resolve_tab_visibility's docstring for why.
#
# Field-level security (udf_field_permissions) is layered on TOP of tab
# visibility as of Sprint udf01c — tab-hidden still wins outright, but a
# visible tab can now further narrow individual fields to hidden/read/edit.
# See services.portfolio_udf_field_permissions's module docstring.


@router.get("/udf/tabs")
async def list_tabs(request: Request, applies_to: str = Query(...)):
    """Tabs visible to the caller, per ``resolve_tab_visibility`` — a tab
    hidden by either grant path is never returned, the same non-bypassing
    principle RLS uses (filtered server-side, not left for the client to
    hide)."""
    org_id, user_id, pool = await _tenant_gate(request, READ_PERMISSION)
    async with pool.acquire() as conn:
        try:
            rows = await list_visible_tabs(
                conn, org_id=org_id, user_id=user_id, applies_to=applies_to
            )
        except _VALIDATION_ERRORS as exc:
            raise _http_error(exc) from exc
    perms = await _permission_envelope(pool, user_id, org_id)
    return {"rows": rows, "permissions": perms, "vocabularies": _tab_vocabularies(perms)}


@router.post("/udf/tabs", status_code=201)
async def create_tab_route(request: Request, body: TabCreateIn):
    org_id, user_id, pool = await _tenant_gate(request, WRITE_PERMISSION)
    async with pool.acquire() as conn:
        try:
            tab = await create_tab(
                conn, org_id=org_id, applies_to=body.applies_to, label=body.label,
                api_name=body.api_name, display_order=body.display_order,
                created_by=user_id,
            )
        except _VALIDATION_ERRORS as exc:
            raise _http_error(exc) from exc
    return {"tab": tab}


@router.patch("/udf/tabs/{tab_id}")
async def patch_tab(request: Request, tab_id: str, body: TabUpdateIn):
    org_id, user_id, pool = await _tenant_gate(request, WRITE_PERMISSION)
    changes = body.model_dump(exclude_unset=True)
    async with pool.acquire() as conn:
        try:
            tab = await update_tab(conn, tab_id=tab_id, org_id=org_id, changes=changes)
        except _VALIDATION_ERRORS as exc:
            raise _http_error(exc) from exc
    return {"tab": tab}


@router.delete("/udf/tabs/{tab_id}")
async def delete_tab(request: Request, tab_id: str):
    """Soft delete. 409 with the reference list if the tab has a non-empty
    layout — see ``TabReferencedError``."""
    org_id, user_id, pool = await _tenant_gate(request, WRITE_PERMISSION)
    async with pool.acquire() as conn:
        try:
            tab = await soft_delete_tab(conn, tab_id=tab_id, org_id=org_id)
        except _VALIDATION_ERRORS as exc:
            raise _http_error(exc) from exc
    return {"tab": tab}


@router.post("/udf/tabs/{tab_id}/deactivate")
async def deactivate_tab_route(request: Request, tab_id: str):
    org_id, user_id, pool = await _tenant_gate(request, WRITE_PERMISSION)
    async with pool.acquire() as conn:
        try:
            tab = await deactivate_tab(conn, tab_id=tab_id, org_id=org_id)
        except _VALIDATION_ERRORS as exc:
            raise _http_error(exc) from exc
    return {"tab": tab}


@router.post("/udf/tabs/{tab_id}/reactivate")
async def reactivate_tab_route(request: Request, tab_id: str):
    """Not in the sprint's endpoint list verbatim, but Task 2b requires
    deactivate to be reversible (``reactivate_tab`` exists in the service
    layer) and there is no way to invoke that reversal without a route — same
    reasoning udf01a's own ``/undelete`` route documented."""
    org_id, user_id, pool = await _tenant_gate(request, WRITE_PERMISSION)
    async with pool.acquire() as conn:
        try:
            tab = await reactivate_tab(conn, tab_id=tab_id, org_id=org_id)
        except _VALIDATION_ERRORS as exc:
            raise _http_error(exc) from exc
    return {"tab": tab}


@router.post("/udf/tabs/{tab_id}/undelete")
async def undelete_tab_route(request: Request, tab_id: str):
    org_id, user_id, pool = await _tenant_gate(request, WRITE_PERMISSION)
    async with pool.acquire() as conn:
        try:
            tab = await undelete_tab(conn, tab_id=tab_id, org_id=org_id)
        except _VALIDATION_ERRORS as exc:
            raise _http_error(exc) from exc
    return {"tab": tab}


@router.put("/udf/tabs/{tab_id}/permissions")
async def put_tab_permissions(request: Request, tab_id: str, body: TabPermissionIn):
    org_id, user_id, pool = await _tenant_gate(request, WRITE_PERMISSION)
    async with pool.acquire() as conn:
        try:
            grant = await set_tab_visibility(
                conn, tab_id=tab_id, org_id=org_id, is_visible=body.is_visible,
                profile_id=body.profile_id, permission_set_id=body.permission_set_id,
            )
        except _VALIDATION_ERRORS as exc:
            raise _http_error(exc) from exc
    return {"permission": grant}


# ── Field-level security — udf01c Task 3a ────────────────────────────────────


@router.put("/udf/fields/{definition_id}/permissions")
async def put_field_permissions(request: Request, definition_id: str, body: FieldPermissionIn):
    org_id, user_id, pool = await _tenant_gate(request, WRITE_PERMISSION)
    async with pool.acquire() as conn:
        principal = await load_principal(conn, user_id)
        super_admin = is_super_admin(principal)
        try:
            grant = await set_field_access(
                conn, definition_id=definition_id, access=body.access, org_id=org_id,
                profile_id=body.profile_id, permission_set_id=body.permission_set_id,
                is_super_admin=super_admin, created_by=user_id,
            )
        except _VALIDATION_ERRORS as exc:
            raise _http_error(exc) from exc
    return {"permission": grant}


@router.get("/udf/fields/{definition_id}/permissions")
async def list_field_permissions(request: Request, definition_id: str):
    """Admin visibility into every current grant for one field — gated on
    the same WRITE_PERMISSION as configuring the grants themselves, since
    there is no separate 'view FLS config' permission string in this
    router's model."""
    org_id, user_id, pool = await _tenant_gate(request, WRITE_PERMISSION)
    async with pool.acquire() as conn:
        rows = await list_field_grants(conn, definition_id=definition_id)
    return {"rows": rows}


# ── Layouts — udf01b Task 3a ─────────────────────────────────────────────────


@router.get("/udf/layouts/{tab_id}")
async def get_layout_route(
    request: Request, tab_id: str, record_type_id: str | None = Query(default=None),
):
    """Resolved section -> item tree, PositionsGrid-compatible envelope
    (``rows``/``permissions``/``vocabularies`` at the top level, plus the
    layout's own ``layout_id``/``tab_id``/``sections`` keys). 403, not an
    empty layout, if the tab is hidden for this caller."""
    org_id, user_id, pool = await _tenant_gate(request, READ_PERMISSION)
    async with pool.acquire() as conn:
        tab = await get_tab(conn, tab_id=tab_id)
        if tab is None:
            raise HTTPException(status_code=404, detail=f"tab {tab_id} not found")
        visible = await resolve_tab_visibility(
            conn, tab_id=tab_id, org_id=org_id, user_id=user_id
        )
        if not visible:
            raise HTTPException(status_code=403, detail="this tab is hidden for the caller")
        try:
            resolved = await get_resolved_layout(
                conn, tab_id=tab_id, org_id=org_id, user_id=user_id,
                record_type_id=record_type_id,
            )
        except _VALIDATION_ERRORS as exc:
            raise _http_error(exc) from exc
    perms = await _permission_envelope(pool, user_id, org_id)
    return {
        **resolved, "rows": resolved["sections"],
        "permissions": perms, "vocabularies": _layout_vocabularies(perms),
    }


@router.post("/udf/layouts/{tab_id}/sections", status_code=201)
async def create_section(request: Request, tab_id: str, body: SectionCreateIn):
    """Lazily creates the tab's (single, default) layout on first use — see
    ``get_or_create_layout``."""
    org_id, user_id, pool = await _tenant_gate(request, WRITE_PERMISSION)
    async with pool.acquire() as conn:
        try:
            layout = await get_or_create_layout(
                conn, org_id=org_id, tab_id=tab_id,
                record_type_id=body.record_type_id, created_by=user_id,
            )
            section = await add_section(
                conn, layout_id=layout["id"], org_id=org_id, title=body.title,
                column_count=body.column_count, display_order=body.display_order,
            )
        except _VALIDATION_ERRORS as exc:
            raise _http_error(exc) from exc
    return {"section": section}


@router.delete("/udf/layouts/{tab_id}/sections/{section_id}")
async def delete_section(request: Request, tab_id: str, section_id: str):
    """Refused, not cascaded, while the section still has items — see
    ``remove_section``."""
    org_id, user_id, pool = await _tenant_gate(request, WRITE_PERMISSION)
    async with pool.acquire() as conn:
        try:
            await remove_section(conn, section_id=section_id, org_id=org_id)
        except _VALIDATION_ERRORS as exc:
            raise _http_error(exc) from exc
    return {"deleted": True}


@router.post("/udf/layouts/{tab_id}/sections/{section_id}/items", status_code=201)
async def create_item(request: Request, tab_id: str, section_id: str, body: ItemCreateIn):
    org_id, user_id, pool = await _tenant_gate(request, WRITE_PERMISSION)
    async with pool.acquire() as conn:
        try:
            item = await add_item(
                conn, section_id=section_id, org_id=org_id,
                definition_id=body.definition_id, display_order=body.display_order,
                column_index=body.column_index, col_span=body.col_span,
                is_read_only=body.is_read_only,
            )
        except _VALIDATION_ERRORS as exc:
            raise _http_error(exc) from exc
    return {"item": item}


@router.patch("/udf/layouts/{tab_id}/items/{item_id}")
async def patch_item(request: Request, tab_id: str, item_id: str, body: ItemUpdateIn):
    org_id, user_id, pool = await _tenant_gate(request, WRITE_PERMISSION)
    changes = body.model_dump(exclude_unset=True)
    async with pool.acquire() as conn:
        try:
            item = await move_item(conn, item_id=item_id, org_id=org_id, **changes)
        except _VALIDATION_ERRORS as exc:
            raise _http_error(exc) from exc
    return {"item": item}


@router.delete("/udf/layouts/{tab_id}/items/{item_id}")
async def delete_item(request: Request, tab_id: str, item_id: str):
    org_id, user_id, pool = await _tenant_gate(request, WRITE_PERMISSION)
    async with pool.acquire() as conn:
        try:
            await remove_item(conn, item_id=item_id, org_id=org_id)
        except _VALIDATION_ERRORS as exc:
            raise _http_error(exc) from exc
    return {"deleted": True}
