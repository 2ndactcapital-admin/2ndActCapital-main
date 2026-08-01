"""Pre-auth tenant resolution endpoint — Headless Multi-Tenant Sprint 1.

    GET /tenant/resolve   resolve the request's Host header to a tenant org

Public by design (like /theme/public): it runs BEFORE anyone has a token, so a
future per-tenant SAML-connection-selection step can know which org a login
attempt is for. This sprint only RESOLVES the org from the subdomain; it does
not yet act on it (no SAML wiring). The resolver falls back to the default org
for the bare domain, an unknown subdomain, or a malformed Host, so 2nd Act's
own operation is unchanged.
"""

from fastapi import APIRouter, Request

from services.database import get_pool
from services.tenant import resolve_tenant

router = APIRouter(tags=["tenant"])


@router.get("/tenant/resolve")
async def resolve(request: Request):
    """Resolve the caller's Host header to a tenant org (pre-auth-safe)."""
    host = request.headers.get("host")
    pool = await get_pool()
    async with pool.acquire() as conn:
        tenant = await resolve_tenant(conn, host)
    return tenant
