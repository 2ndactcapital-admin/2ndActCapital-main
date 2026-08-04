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
async def resolve(request: Request, host: str | None = None):
    """Resolve a Host to a tenant org (pre-auth-safe).

    The Next.js frontend runs server-side, so a plain fetch to this endpoint
    would carry the API's own Host, not the browser's. It therefore forwards the
    real browser host as the ``?host=`` query param, which takes precedence here.
    Absent the param we fall back to the request's own Host header (direct/curl
    callers). Either way resolution is identical and never raises.
    """
    effective_host = host or request.headers.get("host")
    pool = await get_pool()
    async with pool.acquire() as conn:
        tenant = await resolve_tenant(conn, effective_host)
    return tenant
