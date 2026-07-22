"""Org (white-label) settings endpoints — Sprint 24.

    GET    /orgs                            list orgs (super_admin only)
    POST   /orgs                            create an org (super_admin only)
    GET    /orgs/{org_id}/settings          resolved settings for one org
    PUT    /orgs/{org_id}/settings          bulk upsert
    PUT    /orgs/{org_id}/settings/{key}    upsert one key
    GET    /theme                           the caller's own org theme

Reads are open to any authenticated user of the org (the app cannot render its
theme otherwise); reading *another* org requires super_admin. Writes go through
``can_manage_org_settings``.
"""

import re

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from routers.entities import DEFAULT_ORG_ID, get_org_id
from services.database import get_pool
from services.org_settings import (
    DEFAULT_SETTINGS,
    SettingsPermissionError,
    get_all_settings,
    get_public_settings,
    get_settings_detail,
    set_setting,
    set_settings,
)
from services.rbac import is_super_admin, load_principal
from services.users import ensure_user

router = APIRouter(tags=["org-settings"])

SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class SettingValue(BaseModel):
    value: object = None


class SettingsBulk(BaseModel):
    values: dict


class OrgCreate(BaseModel):
    name: str
    slug: str


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
    slug = body.slug.strip().lower()
    if not name:
        raise HTTPException(status_code=400, detail="Organization name is required")
    if not SLUG_RE.match(slug):
        raise HTTPException(
            status_code=400,
            detail="Slug must be lowercase alphanumeric words separated by hyphens",
        )

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
    return {"org_id": org_id, "key": key, "value": value}


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
