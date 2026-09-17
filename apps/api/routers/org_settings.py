"""Org (white-label) settings endpoints — Sprint 24.

    GET    /orgs                                        list orgs (super_admin only)
    POST   /orgs                                        create an org (super_admin only)
    GET    /orgs/{org_id}/settings                      resolved settings for one org
    PUT    /orgs/{org_id}/settings                      bulk upsert
    PUT    /orgs/{org_id}/settings/{key}                upsert one key
    GET    /orgs/{org_id}/settings/ai-credentials        per-provider credential status
    PUT    /orgs/{org_id}/settings/ai-credentials/{provider}     supply the org's own key
    DELETE /orgs/{org_id}/settings/ai-credentials/{provider}     revert to the platform key
    GET    /admin/model-catalog                          curated platform model list
    POST   /admin/model-catalog                           add a model (super_admin only)
    DELETE /admin/model-catalog/{model_id}                remove a model (super_admin only)
    GET    /orgs/{org_id}/settings/model-selections       this org's authorised models
    PUT    /orgs/{org_id}/settings/model-selections       replace this org's selection
    GET    /admin/ai/force-anthropic-bypass               the platform bypass's current state (super_admin only)
    PUT    /admin/ai/force-anthropic-bypass               toggle it (super_admin only)
    GET    /orgs/{org_id}/settings/ai-spend               this org's AI budget config + cached spend status
    GET    /admin/ai/spend-ceiling                        the Hollisworks-wide ceiling's status (super_admin only)
    PUT    /admin/ai/spend-ceiling                        set/clear the ceiling (super_admin only)
    GET    /theme                                        the caller's own org theme

Reads are open to any authenticated user of the org (the app cannot render its
theme otherwise); reading *another* org requires super_admin. Writes go through
``can_manage_org_settings``.

The ai-credentials routes (LiteLLM Phase D1a) are deliberately separate from
the generic settings PUT: they provision/deprovision a real LiteLLM model
deployment as part of the same call (services.litellm_credentials), which the
generic key/value settings path has no business doing. Their responses never
include a deployment name or id — that is LiteLLM-internal, never org-facing.

The model-catalog / model-selections routes (LiteLLM Phase D2) are a
DIFFERENT, higher layer than ai-credentials: which models are supportable at
all (Hollisworks, platform-wide, services.model_catalog.platform_model_catalog)
and which of those one org may actually use
(services.model_catalog.org_model_selections) — orthogonal to which provider
KEY serves a call. /admin/model-catalog is not org-scoped in its URL on
purpose: the curated list is the same for every org.
"""

import asyncpg
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from routers.entities import DEFAULT_ORG_ID, get_org_id
from services.database import get_pool
from services.litellm_credentials import (
    PROVIDER_PLATFORM_DEPLOYMENT,
    CredentialProvisionError,
    clear_org_provider_credential,
    get_credential_status,
    set_org_provider_credential,
)
from services.model_catalog import (
    ModelCatalogError,
    add_catalog_model,
    enrich_with_live_info,
    get_orgs_selecting,
    get_task_assignments,
    list_catalog,
    list_catalog_for_org,
    list_org_selections,
    remove_catalog_model,
    set_catalog_availability,
    set_org_selections,
    set_task_assignment,
)
from services.org_settings import (
    DEFAULT_SETTINGS,
    SettingsPermissionError,
    SettingsValidationError,
    get_all_settings,
    get_public_settings,
    get_settings_detail,
    set_setting,
    set_settings,
)
from services.platform_ai_controls import (
    PlatformAIControlError,
    get_force_anthropic_bypass,
    set_force_anthropic_bypass,
)
from services.rbac import can_manage_org_settings, is_super_admin, load_principal
from services.tenant import SlugValidationError, validate_slug
from services.users import ensure_user

router = APIRouter(tags=["org-settings"])


class SettingValue(BaseModel):
    value: object = None


class SettingsBulk(BaseModel):
    values: dict


class ProviderCredential(BaseModel):
    api_key: str

    class Config:
        extra = "forbid"


class OrgCreate(BaseModel):
    name: str
    slug: str


class CatalogModelCreate(BaseModel):
    model_id: str
    display_name: str
    provider: str

    class Config:
        extra = "forbid"


class ModelSelectionsBody(BaseModel):
    model_ids: list[str]

    class Config:
        extra = "forbid"


class CatalogAvailabilityBody(BaseModel):
    availability: str

    class Config:
        extra = "forbid"


class TaskAssignmentBody(BaseModel):
    model_id: str | None = None
    effort: str | None = None

    class Config:
        extra = "forbid"


class ForceAnthropicBypassBody(BaseModel):
    enabled: bool

    class Config:
        extra = "forbid"


class PlatformCeilingBody(BaseModel):
    ceiling_usd: float | None = None
    warning_pct: float | None = None

    class Config:
        extra = "forbid"


async def _principal(conn, request: Request) -> dict:
    """Resolve the caller to {id, org_id, role}, creating the users row if new."""
    user_id = await ensure_user(conn, request)
    principal = await load_principal(conn, user_id)
    if principal is None:
        # ensure_user fell back to a token-derived id with no row behind it.
        principal = {"id": user_id, "org_id": get_org_id(request), "role": None}
    return principal


def _require_read_access(principal: dict, org_id: str) -> None:
    """Any member of the org may read it; crossing orgs requires super_admin."""
    if is_super_admin(principal):
        return
    if str(principal.get("org_id")) != str(org_id):
        raise HTTPException(
            status_code=403, detail="Not a member of the requested organization"
        )


@router.get("/orgs")
async def list_orgs(request: Request):
    """List every organization. Super Admin only — this is the tenant roster."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        principal = await _principal(conn, request)
        if not is_super_admin(principal):
            raise HTTPException(status_code=403, detail="Super Admin access required")
        rows = await conn.fetch(
            "SELECT id, name, slug, created_at FROM organizations ORDER BY name"
        )
    return {
        "orgs": [
            {
                "id": str(r["id"]),
                "name": r["name"],
                "slug": r["slug"],
                "created_at": r["created_at"],
            }
            for r in rows
        ]
    }


@router.post("/orgs", status_code=201)
async def create_org(request: Request, body: OrgCreate):
    """Create a tenant org. Onboarding a Ripasso client starts here."""
    name = body.name.strip()
    slug = body.slug.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Organization name is required")
    # A slug is about to become a LIVE public subdomain, so validate it is
    # genuinely DNS-safe and not a reserved platform host. Uppercase/special
    # input is REJECTED, not coerced — the caller must supply exactly the
    # subdomain they will get.
    try:
        validate_slug(slug)
    except SlugValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    pool = await get_pool()
    async with pool.acquire() as conn:
        principal = await _principal(conn, request)
        if not is_super_admin(principal):
            raise HTTPException(status_code=403, detail="Super Admin access required")

        exists = await conn.fetchval(
            "SELECT 1 FROM organizations WHERE slug = $1", slug
        )
        if exists:
            raise HTTPException(status_code=409, detail=f"Slug '{slug}' already exists")

        row = await conn.fetchrow(
            "INSERT INTO organizations (name, slug) VALUES ($1, $2) "
            "RETURNING id, name, slug, created_at",
            name, slug,
        )

    # A brand-new org has no rows in org_settings; it renders from
    # DEFAULT_SETTINGS until its Org Admin configures branding.
    return {
        "id": str(row["id"]),
        "name": row["name"],
        "slug": row["slug"],
        "created_at": row["created_at"],
    }


@router.get("/orgs/{org_id}/settings")
async def read_org_settings(request: Request, org_id: str, detail: bool = False):
    pool = await get_pool()
    async with pool.acquire() as conn:
        principal = await _principal(conn, request)
        _require_read_access(principal, org_id)
        if detail:
            return {"org_id": org_id, "settings": await get_settings_detail(conn, org_id)}
        return {"org_id": org_id, "settings": await get_all_settings(conn, org_id)}


@router.get("/orgs/{org_id}/settings/embedding-reindex-estimate")
async def embedding_reindex_estimate(request: Request, org_id: str, new_model: str):
    """Real corpus size + re-indexing cost estimate for the Phase-C friction
    dialog (CLAUDE.md's embedding-compatibility rule). Read access only — the
    actual setting write still goes through the normal PUT permission check;
    this endpoint informs the confirmation, it does not gate anything itself.
    """
    from services.document_embedding import reindex_estimate

    pool = await get_pool()
    async with pool.acquire() as conn:
        principal = await _principal(conn, request)
        _require_read_access(principal, org_id)
        estimate = await reindex_estimate(conn, org_id, new_model)
    return estimate


@router.get("/orgs/{org_id}/settings/model-selections")
async def read_org_model_selections(request: Request, org_id: str):
    """This org's authorised subset of the curated list, plus the vocabulary
    of what it may pick from — never the raw LiteLLM catalogue (Rule 1's
    permission-envelope pattern: editable comes from the server, always).

    MUST be registered before the generic ``PUT /orgs/{org_id}/settings/{key}``
    route below — Starlette matches path routes in REGISTRATION order, and
    ``{key}`` would otherwise swallow a literal ``model-selections`` segment
    (confirmed live: this collision silently routed every model-selections
    write into the generic settings key/value store instead).
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        principal = await _principal(conn, request)
        _require_read_access(principal, org_id)
        selected = await list_org_selections(conn, org_id)
        catalog = await list_catalog_for_org(conn, org_id)
    can_write = await can_manage_org_settings(pool, principal, org_id)
    return {
        "org_id": org_id,
        "selected_model_ids": selected,
        "permissions": {
            "can_read": True,
            "can_write": can_write,
            "is_super_admin": is_super_admin(principal),
        },
        "vocabularies": {
            "editable": [m["model_id"] for m in catalog] if can_write else [],
            "catalog": catalog,
        },
    }


@router.put("/orgs/{org_id}/settings/model-selections")
async def write_org_model_selections(request: Request, org_id: str, body: ModelSelectionsBody):
    pool = await get_pool()
    async with pool.acquire() as conn:
        principal = await _principal(conn, request)
        await _require_write_access(pool, principal, org_id)
        try:
            selected = await set_org_selections(
                conn, org_id, body.model_ids, updated_by=principal["id"],
            )
        except ModelCatalogError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"org_id": org_id, "selected_model_ids": selected}


@router.get("/orgs/{org_id}/settings/ai-tasks")
async def read_task_assignments(request: Request, org_id: str):
    """LiteLLM Phase E — per-task model + effort assignment.

    MUST be registered before the generic ``PUT /orgs/{org_id}/settings/{key}``
    route below, same reason model-selections is (Starlette matches path
    routes in registration order; a single-segment literal can otherwise be
    swallowed by a later ``{key}`` route of the same method) — this GET has
    no colliding generic GET/{key} route today, but keeping every settings
    sub-route registered together, ahead of the generic PUT, is the
    established convention rather than something to re-verify per route.

    Rule 1's permission-envelope pattern: ``vocabularies.editable`` (the
    catalog models this org may actually pick) is an EMPTY array for a
    view-only caller, never omitted — the frontend's task-assignment
    dropdowns render read-only from ``tasks`` alone when ``can_write`` is
    false.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        principal = await _principal(conn, request)
        _require_read_access(principal, org_id)
        tasks = await get_task_assignments(conn, org_id)
        catalog = await list_catalog_for_org(conn, org_id)
        selected = await list_org_selections(conn, org_id)
    can_write = await can_manage_org_settings(pool, principal, org_id)
    # Unrestricted (no explicit org_model_selections row) means every catalog
    # entry is assignable — the same set _execute_chain would actually try.
    assignable = catalog if not selected else [m for m in catalog if m["model_id"] in selected]
    return {
        "org_id": org_id,
        "tasks": tasks,
        "permissions": {
            "can_read": True,
            "can_write": can_write,
            "is_super_admin": is_super_admin(principal),
        },
        "vocabularies": {
            "editable": [m["model_id"] for m in assignable] if can_write else [],
            "assignable_models": assignable if can_write else [],
            "effort_levels": [
                {"value": "low", "label": "Low"},
                {"value": "medium", "label": "Medium"},
                {"value": "high", "label": "High"},
            ] if can_write else [],
        },
    }


@router.put("/orgs/{org_id}/settings/ai-tasks/{task_key:path}")
async def write_task_assignment(
    request: Request, org_id: str, task_key: str, body: TaskAssignmentBody
):
    """Assign a model (and optional effort) to one task dial.

    ``task_key`` carries dots (``ai.model.default``) — ``:path`` so Starlette
    does not treat them as separate segments. Validation (catalog
    authorization + effort enum) and the ``manage_org_settings`` permission
    check both happen inside ``set_task_assignment`` via the shared
    ``services.org_settings.set_setting`` — the identical gate every other
    settings write in this router uses, never a second, bespoke one.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        principal = await _principal(conn, request)
        try:
            result = await set_task_assignment(
                conn, pool, org_id, task_key,
                model_id=body.model_id, effort=body.effort,
                updated_by=principal["id"], principal=principal,
            )
        except SettingsPermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except (SettingsValidationError, ModelCatalogError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"org_id": org_id, **result}


@router.get("/orgs/{org_id}/settings/ai-spend")
async def read_org_ai_spend(request: Request, org_id: str):
    """LiteLLM Phase G — this org's AI spend budget config + cached
    current-period spend status.

    Read-only: the CONFIG half (``ai.budget.monthly_usd`` /
    ``ai.budget.warning_pct``) is written through the existing generic
    ``PUT /orgs/{org_id}/settings/{key}`` — the same ``manage_org_settings``
    gate and envelope every other ``ai.*`` setting already uses, so no new
    write endpoint is needed. This endpoint only adds the piece the generic
    settings read cannot: the cached SPEND number, refreshed by the
    periodic sync job (services.ai_budgets), never by this read.
    """
    from services.ai_budgets import get_org_spend_status

    pool = await get_pool()
    async with pool.acquire() as conn:
        principal = await _principal(conn, request)
        _require_read_access(principal, org_id)
        status = await get_org_spend_status(conn, org_id)
    can_write = await can_manage_org_settings(pool, principal, org_id)
    return {
        "org_id": org_id,
        **status,
        "permissions": {
            "can_read": True,
            "can_write": can_write,
            "is_super_admin": is_super_admin(principal),
        },
    }


@router.put("/orgs/{org_id}/settings")
async def write_org_settings(request: Request, org_id: str, body: SettingsBulk):
    pool = await get_pool()
    async with pool.acquire() as conn:
        principal = await _principal(conn, request)
        try:
            settings = await set_settings(
                conn, org_id, body.values, principal["id"], principal=principal
            )
        except SettingsPermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except SettingsValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"org_id": org_id, "settings": settings}


@router.put("/orgs/{org_id}/settings/{key}")
async def write_org_setting(
    request: Request, org_id: str, key: str, body: SettingValue
):
    pool = await get_pool()
    async with pool.acquire() as conn:
        principal = await _principal(conn, request)
        try:
            value = await set_setting(
                conn, org_id, key, body.value, principal["id"], principal=principal
            )
        except SettingsPermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except SettingsValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"org_id": org_id, "key": key, "value": value}


async def _require_write_access(pool, principal: dict, org_id: str) -> None:
    """Same gate services.org_settings.set_setting enforces internally —
    duplicated here (not imported) only because these two endpoints need to
    check it BEFORE calling out to LiteLLM's admin API, whereas set_setting
    checks it as its own first step. Both call the identical
    can_manage_org_settings helper, so a 403 here means the same thing a
    settings 403 always means."""
    if not await can_manage_org_settings(pool, principal, org_id):
        raise HTTPException(
            status_code=403,
            detail=f"Not permitted to manage settings for org {org_id}",
        )


@router.get("/orgs/{org_id}/settings/ai-credentials")
async def read_ai_credential_status(request: Request, org_id: str):
    """Per-provider credential source for this org — 'org' or 'platform'.

    Never returns a deployment name or id (Task 4's proof for LiteLLM Phase
    D1a) — that is LiteLLM-internal plumbing, not something an org sees.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        principal = await _principal(conn, request)
        _require_read_access(principal, org_id)
        statuses = [
            await get_credential_status(conn, org_id, provider)
            for provider in PROVIDER_PLATFORM_DEPLOYMENT
        ]
    return {"org_id": org_id, "credentials": statuses}


@router.put("/orgs/{org_id}/settings/ai-credentials/{provider}")
async def write_ai_credential(
    request: Request, org_id: str, provider: str, body: ProviderCredential
):
    """Supply the org's own provider key. Provisions a dedicated LiteLLM
    deployment and flips ai.credential_source.{provider} to 'org' — see
    services.litellm_credentials.set_org_provider_credential."""
    if provider not in PROVIDER_PLATFORM_DEPLOYMENT:
        raise HTTPException(status_code=400, detail=f"Unsupported provider: {provider}")
    pool = await get_pool()
    async with pool.acquire() as conn:
        principal = await _principal(conn, request)
        await _require_write_access(pool, principal, org_id)
        try:
            status = await set_org_provider_credential(
                conn, pool, org_id, provider, body.api_key, principal["id"],
                principal=principal,
            )
        except CredentialProvisionError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"org_id": org_id, **status}


@router.delete("/orgs/{org_id}/settings/ai-credentials/{provider}")
async def delete_ai_credential(request: Request, org_id: str, provider: str):
    """Remove the org's own provider key. Deprovisions its dedicated LiteLLM
    deployment and reverts ai.credential_source.{provider} to 'platform' —
    see services.litellm_credentials.clear_org_provider_credential."""
    if provider not in PROVIDER_PLATFORM_DEPLOYMENT:
        raise HTTPException(status_code=400, detail=f"Unsupported provider: {provider}")
    pool = await get_pool()
    async with pool.acquire() as conn:
        principal = await _principal(conn, request)
        await _require_write_access(pool, principal, org_id)
        try:
            status = await clear_org_provider_credential(
                conn, pool, org_id, provider, principal["id"], principal=principal,
            )
        except CredentialProvisionError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"org_id": org_id, **status}


# ── LiteLLM Phase D2 — model pick-list ──────────────────────────────────────
# TWO TIERS (see services.model_catalog's module docstring): Hollisworks
# curates /admin/model-catalog (super_admin only writes); an org picks its own
# subset via /orgs/{org_id}/settings/model-selections (manage_org_settings
# writes — the identical envelope the ai-credentials endpoints above use).


@router.get("/admin/model-catalog")
async def read_model_catalog(request: Request):
    """The curated platform list. Read by any authenticated user (an org
    member's picker screen needs it too) — only writes are super_admin only."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        principal = await _principal(conn, request)
        rows = await list_catalog(conn)
    enriched = await enrich_with_live_info(rows)
    return {
        "models": enriched,
        "permissions": {
            "can_read": True,
            "can_write": is_super_admin(principal),
            "is_super_admin": is_super_admin(principal),
        },
    }


@router.post("/admin/model-catalog", status_code=201)
async def create_model_catalog_entry(request: Request, body: CatalogModelCreate):
    pool = await get_pool()
    async with pool.acquire() as conn:
        principal = await _principal(conn, request)
        if not is_super_admin(principal):
            raise HTTPException(status_code=403, detail="Super Admin access required")
        try:
            row = await add_catalog_model(
                conn, model_id=body.model_id, display_name=body.display_name,
                provider=body.provider, created_by=principal["id"],
            )
        except ModelCatalogError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    return row


@router.delete("/admin/model-catalog/{model_id}")
async def delete_model_catalog_entry(request: Request, model_id: str):
    pool = await get_pool()
    async with pool.acquire() as conn:
        principal = await _principal(conn, request)
        if not is_super_admin(principal):
            raise HTTPException(status_code=403, detail="Super Admin access required")
        try:
            removed = await remove_catalog_model(conn, model_id)
        except ModelCatalogError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not removed:
        raise HTTPException(status_code=404, detail=f"'{model_id}' is not on the platform catalog")
    return {"model_id": model_id, "removed": True}


@router.put("/admin/model-catalog/{model_id}/availability")
async def set_model_catalog_availability(
    request: Request, model_id: str, body: CatalogAvailabilityBody
):
    """LiteLLM D2 §3 — Hollisworks super-admin transitions one catalog
    model's availability. Setting 'deprecated' or 'disabled' alerts every
    org that currently has ``model_id`` selected (never every org) —
    ``services.workflow_todos.create_model_availability_alerts``, the same
    ``manage_org_settings`` recipient rule and ``_upsert_todo`` /
    ``_record_undelivered_alert`` machinery D1c's credential-failure alert
    already established. An invalid ``availability`` value is refused by the
    real DB CHECK constraint, not a duplicated app-side enum.
    """
    from services.workflow_todos import create_model_availability_alerts

    pool = await get_pool()
    async with pool.acquire() as conn:
        principal = await _principal(conn, request)
        if not is_super_admin(principal):
            raise HTTPException(status_code=403, detail="Super Admin access required")
        try:
            result = await set_catalog_availability(
                conn, model_id=model_id, availability=body.availability,
            )
        except ModelCatalogError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except asyncpg.CheckViolationError as exc:
            raise HTTPException(
                status_code=400,
                detail=f"'{body.availability}' is not a valid availability value",
            ) from exc

        if result["availability"] in ("deprecated", "disabled"):
            affected_org_ids = await get_orgs_selecting(conn, model_id)
            for affected_org_id in affected_org_ids:
                await create_model_availability_alerts(
                    conn, org_id=affected_org_id, model_id=model_id,
                    availability=result["availability"],
                )
    return result


# LiteLLM Phase F — the Hollisworks-only force-Anthropic emergency bypass
# (services.platform_ai_controls). Genuinely platform-scoped (no org_id
# anywhere in these routes' URL or body — CLAUDE.md Rule 6) and super_admin
# only on BOTH read and write: unlike the model-catalog's curated list, an
# ordinary org member/org_admin has no legitimate reason to see whether
# Hollisworks currently has this emergency switch engaged.


@router.get("/admin/ai/force-anthropic-bypass")
async def read_force_anthropic_bypass(request: Request):
    pool = await get_pool()
    async with pool.acquire() as conn:
        principal = await _principal(conn, request)
        if not is_super_admin(principal):
            raise HTTPException(status_code=403, detail="Super Admin access required")
        try:
            row = await get_force_anthropic_bypass(conn)
        except PlatformAIControlError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {
        "enabled": row["enabled"],
        "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
        "updated_by": str(row["updated_by"]) if row["updated_by"] else None,
        "permissions": {
            "can_read": True,
            "can_write": True,
            "is_super_admin": True,
        },
    }


@router.put("/admin/ai/force-anthropic-bypass")
async def write_force_anthropic_bypass(request: Request, body: ForceAnthropicBypassBody):
    pool = await get_pool()
    async with pool.acquire() as conn:
        principal = await _principal(conn, request)
        if not is_super_admin(principal):
            raise HTTPException(status_code=403, detail="Super Admin access required")
        try:
            row = await set_force_anthropic_bypass(
                conn, enabled=body.enabled, updated_by=principal["id"],
            )
        except PlatformAIControlError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {
        "enabled": row["enabled"],
        "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
        "updated_by": str(row["updated_by"]) if row["updated_by"] else None,
    }


# ── LiteLLM Phase G — the Hollisworks-wide spend ceiling ────────────────────
# Genuinely platform-scoped (services.ai_budgets / platform_ai_controls),
# same super_admin-both-ways gate as force-anthropic-bypass above — an org
# has no legitimate reason to see or influence the platform's own ceiling.
# Per-org budgets are NOT here — they go through the generic settings PUT
# (ai.budget.monthly_usd / ai.budget.warning_pct), since they are ordinary
# per-org config, not a platform-scoped control.


@router.get("/admin/ai/spend-ceiling")
async def read_platform_spend_ceiling(request: Request):
    from services.ai_budgets import AIBudgetError, get_platform_ceiling_status

    pool = await get_pool()
    async with pool.acquire() as conn:
        principal = await _principal(conn, request)
        if not is_super_admin(principal):
            raise HTTPException(status_code=403, detail="Super Admin access required")
        try:
            status = await get_platform_ceiling_status(conn)
        except AIBudgetError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {
        **status,
        "permissions": {"can_read": True, "can_write": True, "is_super_admin": True},
    }


@router.put("/admin/ai/spend-ceiling")
async def write_platform_spend_ceiling(request: Request, body: PlatformCeilingBody):
    from services.ai_budgets import AIBudgetError, set_platform_ceiling

    if body.ceiling_usd is not None and body.ceiling_usd <= 0:
        raise HTTPException(status_code=400, detail="ceiling_usd must be a positive number")
    if body.warning_pct is not None and not (0 < body.warning_pct < 100):
        raise HTTPException(status_code=400, detail="warning_pct must be between 0 and 100")

    pool = await get_pool()
    async with pool.acquire() as conn:
        principal = await _principal(conn, request)
        if not is_super_admin(principal):
            raise HTTPException(status_code=403, detail="Super Admin access required")
        try:
            row = await set_platform_ceiling(
                conn, ceiling_usd=body.ceiling_usd, warning_pct=body.warning_pct,
                updated_by=principal["id"],
            )
        except AIBudgetError as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc
    return {
        "enabled": row["enabled"],
        "ceiling_usd": float(row["numeric_value"]) if row["numeric_value"] is not None else None,
        "warning_pct": float(row["warning_pct"]) if row["warning_pct"] is not None else None,
        "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
        "updated_by": str(row["updated_by"]) if row["updated_by"] else None,
    }


@router.get("/theme/public")
async def read_public_theme(slug: str | None = None):
    """Unauthenticated theme lookup, used to brand the login screen.

    Returns only settings flagged ``is_public`` (branding / footer / naming —
    never anything member-specific). The org is resolved by slug when the
    deployment is host-mapped; otherwise it falls back to the default org.
    """
    pool = await get_pool()
    async with pool.acquire() as conn:
        if slug:
            org = await conn.fetchrow(
                "SELECT id, name, slug FROM organizations WHERE slug = $1", slug
            )
        else:
            org = await conn.fetchrow(
                "SELECT id, name, slug FROM organizations WHERE id = $1",
                DEFAULT_ORG_ID,
            )
        if org is None:
            # Unknown tenant: DEFAULT_SETTINGS still yields a usable shell.
            return {"org_id": None, "org_name": None, "org_slug": slug,
                    "settings": dict(DEFAULT_SETTINGS)}

        settings = await get_public_settings(conn, org["id"])

    return {
        "org_id": str(org["id"]),
        "org_name": org["name"],
        "org_slug": org["slug"],
        "settings": settings,
    }


@router.get("/theme")
async def read_theme(request: Request):
    """The caller's own org settings — hydrates the frontend theme provider."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        principal = await _principal(conn, request)
        org_id = principal.get("org_id") or get_org_id(request)
        settings = await get_all_settings(conn, org_id)
        org = await conn.fetchrow(
            "SELECT name, slug FROM organizations WHERE id = $1", org_id
        )
    return {
        "org_id": str(org_id),
        "org_name": org["name"] if org else None,
        "org_slug": org["slug"] if org else None,
        "role": principal.get("role"),
        "settings": settings,
    }
