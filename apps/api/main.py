"""Ripasso API.

Ripasso is the licensable platform; each client firm is a tenant org whose
branding lives in ``org_settings`` (Sprint 24). Nothing here names a specific
client.

FastAPI application entrypoint. Exposes a public health check and protects
every other route with Auth0-issued JWT validation.
"""

from functools import lru_cache

import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from jose import jwt
from jose.exceptions import JWTError
from pydantic_settings import BaseSettings, SettingsConfigDict

from routers.admin import router as admin_router
from routers.allocation_lens import router as allocation_lens_router
from routers.ledger import router as ledger_router
from routers.assistant import router as assistant_router
from routers.dashboard import router as dashboard_router
from routers.debug import router as debug_router
from routers.entities import router as entities_router, get_org_id
from routers.entity_graph import router as entity_graph_router
from routers.ownership_tree import router as ownership_tree_router
from routers.households import router as households_router
from routers.investment_profile import router as investment_profile_router
from routers.marketplace import router as marketplace_router
from routers.notifications import router as notifications_router
from routers.org_settings import router as org_settings_router
from routers.portfolio import router as portfolio_router
from routers.profiles import router as profiles_router
from routers.entity_documents import router as entity_documents_router
from routers.reference import router as reference_router
from routers.restricted_access import router as restricted_access_router
from routers.spv import router as spv_router
from routers.staff_assignments import router as staff_assignments_router
from routers.trading_authority import router as trading_authority_router
from routers.users import router as users_router
from services.database import (
    close_pool,
    get_pool,
    reset_auth0_sub_context,
    reset_rls_context,
    set_auth0_sub_context,
    set_rls_context,
)
from services.rbac import is_super_admin

API_VERSION = "0.1.0"

# Paths that do not require authentication.
# NOTE: /debug/user-info is intentionally public for production triage — remove
# it (and the debug router) once the ensure_user 500s are confirmed fixed.
# /api/v1/theme/public is public by design: the login screen must render the
# tenant's branding before anyone has a token. It serves only is_public
# settings (colours, fonts, names) — never member data.
PUBLIC_PATHS = {"/health", "/debug/user-info", "/api/v1/theme/public"}


class Settings(BaseSettings):
    """Runtime configuration sourced from environment variables."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    auth0_domain: str = "dev-smmrfubsfscif3t1.us.auth0.com"
    auth0_audience: str = "https://api.2ndactcapital.com"
    # Comma-separated list of allowed CORS origins.  Defaults to local dev;
    # override with ALLOWED_ORIGINS in production to include the Render URL.
    allowed_origins: str = "http://localhost:3000,https://2ndactcapital.com"

    @property
    def issuer(self) -> str:
        return f"https://{self.auth0_domain}/"

    @property
    def jwks_url(self) -> str:
        return f"https://{self.auth0_domain}/.well-known/jwks.json"


@lru_cache
def get_settings() -> Settings:
    return Settings()


@lru_cache
def get_jwks() -> dict:
    """Fetch and cache the Auth0 JSON Web Key Set."""
    settings = get_settings()
    response = httpx.get(settings.jwks_url, timeout=10.0)
    response.raise_for_status()
    return response.json()


def verify_token(token: str) -> dict:
    """Validate a Bearer token against the Auth0 tenant.

    Returns the decoded claims on success and raises ``JWTError`` otherwise.
    """
    settings = get_settings()
    jwks = get_jwks()

    unverified_header = jwt.get_unverified_header(token)
    rsa_key = next(
        (
            {
                "kty": key["kty"],
                "kid": key["kid"],
                "use": key["use"],
                "n": key["n"],
                "e": key["e"],
            }
            for key in jwks.get("keys", [])
            if key["kid"] == unverified_header.get("kid")
        ),
        None,
    )

    if rsa_key is None:
        raise JWTError("Unable to find a matching signing key")

    return jwt.decode(
        token,
        rsa_key,
        algorithms=["RS256"],
        audience=settings.auth0_audience,
        issuer=settings.issuer,
    )


app = FastAPI(title="Ripasso API", version=API_VERSION)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in get_settings().allowed_origins.split(",") if o.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

async def _resolve_is_super_admin(request: Request) -> bool:
    """Best-effort, READ-ONLY resolution of super-admin status for RLS context.

    Reads ``users.role`` by ``auth0_sub`` and reuses ``services.rbac.is_super_admin``.
    Unlike ``ensure_user`` it never inserts — a brand-new user simply resolves to
    not-super, which is the safe default. Any failure returns False.

    Future note: once the app connects as the non-bypass ``app_service`` role,
    this read is itself subject to RLS on ``users`` (RLS enabled, and as of this
    sprint NO policy) — so it will return nothing and every caller is treated as
    not-super. That fails safe (deny) and is exactly the ``users`` carve-out a
    later sprint must design (see ``services.users.ensure_user``).
    """
    claims = getattr(request.state, "user", None) or {}
    sub = claims.get("sub")
    if not sub:
        return False
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT role FROM users WHERE auth0_sub = $1", sub)
    return is_super_admin(dict(row)) if row else False


# NOTE ON ORDERING: this middleware is defined BEFORE auth0_jwt_middleware on
# purpose. Starlette runs the LAST-registered middleware outermost, so defining
# this one first makes it *inner* — it therefore runs AFTER auth0_jwt_middleware
# has populated request.state.user, which we need to resolve org_id.
@app.middleware("http")
async def rls_context_middleware(request: Request, call_next):
    """Populate the per-task RLS ContextVars (org_id + is_super_admin) so that
    services.database's acquire() can SET LOCAL them for Row-Level Security.

    Fails safe: on any resolution error the context is left at its default
    (org unset → default-deny, not super-admin). Reuses the existing
    get_org_id(request) / is_super_admin(principal) logic — nothing reinvented.

    This is inert while the app connects as the RLS-bypassing ``postgres`` role
    (current production). It only takes effect once the connection is switched
    to the non-bypass ``app_service`` role — a separate, manual step.

    ORDERING (RLS Phase 2): ``app.current_auth0_sub`` is established FIRST, from
    the raw JWT ``sub``, BEFORE any read of ``users`` — because the identity
    read below (``_resolve_is_super_admin`` → ``SELECT ... FROM users WHERE
    auth0_sub = $1``) is itself subject to the ``users`` RLS policy once the app
    connects as ``app_service``. Only the bootstrap leg (auth0_sub match) lets
    that self-read succeed when org/role are not yet known. AFTER identity is
    resolved we set org_id/is_super_admin for the rest of the request. This is a
    strict ADDITION to the previous sequence — the existing happy path (org +
    super resolution, default-deny on failure) is unchanged.
    """
    claims = getattr(request.state, "user", None) or {}
    sub = claims.get("sub")

    # 1. Bootstrap identity FIRST — before the users read below and before the
    #    route handler's ensure_user() INSERT. Left unset (→ default-deny) when
    #    there is no authenticated sub (public routes, missing claim).
    sub_token = set_auth0_sub_context(sub)

    org_id = None
    is_super = False
    if claims:
        try:
            org_id = get_org_id(request)
        except Exception as exc:  # never block the request on context resolution
            print(f"[rls] org_id resolution failed (default-deny): {exc}")
            org_id = None
        try:
            # Reads users by auth0_sub — now permitted by the bootstrap leg,
            # since app.current_auth0_sub is already set (step 1 above).
            is_super = await _resolve_is_super_admin(request)
        except Exception as exc:
            print(f"[rls] is_super_admin resolution failed (default False): {exc}")
            is_super = False

    # 2. Now that identity is resolved, set org_id/is_super_admin for the
    #    remainder of the request's queries.
    tokens = set_rls_context(org_id, is_super)
    try:
        return await call_next(request)
    finally:
        reset_rls_context(tokens)
        reset_auth0_sub_context(sub_token)


@app.middleware("http")
async def auth0_jwt_middleware(request: Request, call_next):
    """Require a valid Auth0 JWT for every route except the public ones."""
    # Let CORS preflight and public routes through untouched. The /debug/*
    # prefix is matched explicitly (not just via PUBLIC_PATHS) so the triage
    # endpoints are reachable without a token regardless of exact path — remove
    # this prefix bypass together with the debug router.
    path = request.url.path
    if (
        request.method == "OPTIONS"
        or path in PUBLIC_PATHS
        or path.startswith("/debug/")
    ):
        return await call_next(request)

    auth_header = request.headers.get("Authorization", "")
    scheme, _, token = auth_header.partition(" ")

    if scheme.lower() != "bearer" or not token:
        return JSONResponse(
            status_code=401,
            content={"detail": "Missing or malformed Authorization header"},
        )

    try:
        request.state.user = verify_token(token)
    except JWTError as exc:
        return JSONResponse(status_code=401, content={"detail": f"Invalid token: {exc}"})
    except httpx.HTTPError:
        return JSONResponse(
            status_code=503, content={"detail": "Unable to reach identity provider"}
        )

    return await call_next(request)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "version": API_VERSION}


@app.on_event("startup")
async def _startup() -> None:
    from services.assistant_actions import register_all
    from services.action_registry import REGISTRY
    from services.brief_blocks import register_brief_blocks
    from services.database import get_pool

    register_all()
    register_brief_blocks()
    try:
        pool = await get_pool()
        await REGISTRY.sync_catalog(pool, "00000000-0000-0000-0000-000000000001")
    except Exception as exc:
        print(f"[startup] sync_catalog failed (non-fatal): {exc}")


@app.on_event("shutdown")
async def _shutdown() -> None:
    await close_pool()


# Feature routers
app.include_router(assistant_router, prefix="/api/v1")
app.include_router(dashboard_router, prefix="/api/v1")
app.include_router(entities_router, prefix="/api/v1")
app.include_router(entity_documents_router, prefix="/api/v1")
app.include_router(investment_profile_router, prefix="/api/v1")
app.include_router(marketplace_router, prefix="/api/v1")
app.include_router(portfolio_router, prefix="/api/v1")
app.include_router(spv_router, prefix="/api/v1")
app.include_router(entity_graph_router, prefix="/api/v1")
app.include_router(ownership_tree_router, prefix="/api/v1")
app.include_router(reference_router, prefix="/api/v1")
app.include_router(notifications_router, prefix="/api/v1")
app.include_router(admin_router, prefix="/api/v1")
app.include_router(staff_assignments_router, prefix="/api/v1")
app.include_router(households_router, prefix="/api/v1")
app.include_router(users_router, prefix="/api/v1")
app.include_router(allocation_lens_router, prefix="/api/v1")
app.include_router(ledger_router, prefix="/api/v1")
app.include_router(org_settings_router, prefix="/api/v1")
app.include_router(restricted_access_router, prefix="/api/v1")
app.include_router(trading_authority_router, prefix="/api/v1")
app.include_router(profiles_router, prefix="/api/v1")
# Debug router mounted at root so the path is exactly /debug/user-info.
app.include_router(debug_router)
