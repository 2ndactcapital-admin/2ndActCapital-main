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
from routers.entities import router as entities_router
from routers.entity_graph import router as entity_graph_router
from routers.households import router as households_router
from routers.investment_profile import router as investment_profile_router
from routers.marketplace import router as marketplace_router
from routers.notifications import router as notifications_router
from routers.org_settings import router as org_settings_router
from routers.portfolio import router as portfolio_router
from routers.entity_documents import router as entity_documents_router
from routers.reference import router as reference_router
from routers.spv import router as spv_router
from routers.staff_assignments import router as staff_assignments_router
from routers.users import router as users_router
from services.database import close_pool

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
app.include_router(reference_router, prefix="/api/v1")
app.include_router(notifications_router, prefix="/api/v1")
app.include_router(admin_router, prefix="/api/v1")
app.include_router(staff_assignments_router, prefix="/api/v1")
app.include_router(households_router, prefix="/api/v1")
app.include_router(users_router, prefix="/api/v1")
app.include_router(allocation_lens_router, prefix="/api/v1")
app.include_router(ledger_router, prefix="/api/v1")
app.include_router(org_settings_router, prefix="/api/v1")
# Debug router mounted at root so the path is exactly /debug/user-info.
app.include_router(debug_router)
