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
from routers.custody_import import router as custody_import_router
from routers.dashboard import router as dashboard_router
from routers.debug import router as debug_router
from routers.entities import router as entities_router, get_org_id
from routers.modeling_ta import router as modeling_ta_router
from routers.entity_graph import router as entity_graph_router
from routers.ownership_tree import router as ownership_tree_router
from routers.billing_groups import router as billing_groups_router
from routers.fee_chat import router as fee_chat_router
from routers.fee_schedules import router as fee_schedules_router
from routers.profitability import router as profitability_router
from routers.households import router as households_router
from routers.investment_profile import router as investment_profile_router
from routers.enroll import router as enroll_router
from routers.invites import router as invites_router
from routers.marketing import router as marketing_router
from routers.marketplace import router as marketplace_router
from routers.notifications import router as notifications_router
from routers.org_settings import router as org_settings_router
from routers.portfolio import router as portfolio_router
from routers.portfolio_ingest import router as portfolio_ingest_router
from routers.portfolio_positions import router as portfolio_positions_router
from routers.portfolio_securities import router as portfolio_securities_router
from routers.portfolio_transactions import router as portfolio_transactions_router
from routers.profiles import router as profiles_router
from routers.tenant import router as tenant_router
from routers.udf import router as udf_router
from routers.entity_documents import router as entity_documents_router
from routers.documents import router as documents_router
from routers.document_links import router as document_links_router
from routers.document_review import router as document_review_router
from routers.reference import router as reference_router
from routers.semantic_search import router as semantic_search_router
from routers.restricted_access import router as restricted_access_router
from routers.pricing_admin import router as pricing_admin_router
from routers.pricing_surface import router as pricing_surface_router
from routers.spv import router as spv_router
from routers.staff_assignments import router as staff_assignments_router
from routers.trading_authority import router as trading_authority_router
from routers.users import router as users_router
from routers.vdr import router as vdr_router
from routers.workflows import router as workflows_router
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
PUBLIC_PATHS = {
    "/health",
    "/debug/user-info",
    "/api/v1/theme/public",
    # Pre-auth tenant resolution: must run before anyone has a token so a future
    # per-tenant SAML step knows which org a login attempt is for. Resolves only
    # public org metadata (id/name/slug) — never member data. See routers/tenant.
    "/api/v1/tenant/resolve",
    # Hollisworks marketing surface (platform apex, pre-tenant). Firm-search
    # reads only public org metadata; contact stores an anonymous lead. Both run
    # before anyone has a token. See routers/marketing.
    "/api/v1/marketing/firm-search",
    "/api/v1/marketing/contact",
    # Invite redemption, pre-auth by necessity: an invitee has no session yet —
    # that is what an invite IS — so /enroll must be able to classify the token
    # before Auth0 is involved. The token is the credential; the endpoint returns
    # only the org's public name/slug and the address the invite was sent to, and
    # writes nothing. The matching WRITE (/api/v1/enroll/accept) is deliberately
    # NOT public: it needs a verified Auth0 sub. See routers/enroll.
    "/api/v1/enroll/validate",
}


class Settings(BaseSettings):
    """Runtime configuration sourced from environment variables."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    auth0_domain: str = "dev-smmrfubsfscif3t1.us.auth0.com"
    auth0_audience: str = "https://api.2ndactcapital.com"
    # Second, SEPARATE Auth0 tenant — Hollisworks platform-staff tenant, used
    # ONLY for admin.hollisworks.com. Purely additive: when the domain is unset
    # (current production) NOTHING below runs and token validation behaves
    # exactly as it did for the single 2nd Act tenant.
    #
    # AUDIENCE (fixed this sprint): the Hollisworks tenant mints staff tokens for
    # its OWN API, https://api.hollisworks.com — NOT 2nd Act's audience. This MUST
    # stay in lockstep with the frontend's HOLLISWORKS_API_AUDIENCE
    # (apps/web/lib/authHostConfig.mjs); the frontend requests that audience at
    # /authorize and this backend validates the returned token against the SAME
    # value here. It previously defaulted to https://api.2ndactcapital.com — the
    # exact 2nd-Act-value-leaking bug shape — which the Hollisworks tenant's
    # /authorize rejects with "Service not found", and which would fail audience
    # validation here even if a token were somehow issued. Override per-env with
    # HOLLISWORKS_AUTH0_AUDIENCE.
    hollisworks_auth0_domain: str = ""
    hollisworks_auth0_audience: str = "https://api.hollisworks.com"
    # Comma-separated list of allowed CORS origins.  Defaults to local dev;
    # override with ALLOWED_ORIGINS in production to include the Render URL.
    allowed_origins: str = "http://localhost:3000,https://2ndactcapital.com"

    @property
    def issuer(self) -> str:
        return f"https://{self.auth0_domain}/"

    @property
    def jwks_url(self) -> str:
        return f"https://{self.auth0_domain}/.well-known/jwks.json"

    @property
    def hollisworks_enabled(self) -> bool:
        return bool(self.hollisworks_auth0_domain)

    @property
    def hollisworks_issuer(self) -> str:
        return f"https://{self.hollisworks_auth0_domain}/"

    @property
    def hollisworks_jwks_url(self) -> str:
        return f"https://{self.hollisworks_auth0_domain}/.well-known/jwks.json"


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


@lru_cache
def get_hollisworks_jwks() -> dict:
    """Fetch and cache the Hollisworks tenant JWKS (second, separate tenant)."""
    settings = get_settings()
    response = httpx.get(settings.hollisworks_jwks_url, timeout=10.0)
    response.raise_for_status()
    return response.json()


def _decode_against(token: str, jwks: dict, *, audience: str, issuer: str) -> dict:
    """Decode+validate ``token`` against one tenant's JWKS/audience/issuer.

    Raises ``JWTError`` on any mismatch (unknown kid, bad signature, wrong
    audience/issuer). This is the exact validation the single-tenant path always
    performed — factored out so a second tenant can reuse it verbatim.
    """
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
        audience=audience,
        issuer=issuer,
    )


def is_hollisworks_claims(claims: dict | None) -> bool:
    """True when a validated token was issued by the Hollisworks tenant.

    Platform staff authenticate against the Hollisworks tenant, so its issuer IS
    the Super Admin signal (see ``_resolve_is_super_admin`` and
    ``services.users.ensure_user``). Returns False when Hollisworks is not
    configured, so the 2nd Act path is never treated as staff.
    """
    settings = get_settings()
    if not settings.hollisworks_enabled or not claims:
        return False
    return claims.get("iss") == settings.hollisworks_issuer


def _unverified_issuer(token: str) -> str | None:
    """The ``iss`` claim WITHOUT validating the signature.

    Used only to produce a diagnosable error message (see ``verify_token``).
    Never trusted for an authorization decision — a token that fails validation
    is still rejected regardless of what this returns.
    """
    try:
        return jwt.get_unverified_claims(token).get("iss")
    except Exception:
        return None


def verify_token(token: str) -> dict:
    """Validate a Bearer token against the Auth0 tenant(s).

    The 2nd Act tenant is tried FIRST with its exact original parameters, so a
    2nd Act token takes the identical code path it always did. Only if that
    rejects the token AND the Hollisworks tenant is configured do we additively
    try the Hollisworks tenant (admin.hollisworks.com staff). If both reject,
    the original 2nd Act ``JWTError`` is raised — unchanged failure behavior.

    FAIL LOUD ON A MISSING ENV VAR (superadminmenu sprint). The Hollisworks leg
    is skipped entirely when ``HOLLISWORKS_AUTH0_DOMAIN`` is unset, and the error
    surfaced was 2nd Act's ("Unable to find a matching signing key") — which
    names the wrong tenant and mentions no env var. Every request from a valid
    admin.hollisworks.com session therefore 401'd for an undiagnosable reason,
    and since ``ensure_user`` only runs INSIDE route handlers, no ``users`` row
    was ever created for platform staff. This is the fourth instance of the same
    bug shape as ``domain ?? AUTH0_DOMAIN``, ``appBaseUrl ?? APP_BASE_URL`` and
    ``audience || <2nd Act>`` — a silent config gap that is indistinguishable
    from a working one. When the token self-identifies as Hollisworks-issued but
    the tenant is unconfigured, we now say exactly that.

    Returns the decoded claims on success and raises ``JWTError`` otherwise.
    """
    settings = get_settings()

    # 1. Existing 2nd Act tenant — unchanged.
    try:
        return _decode_against(
            token,
            get_jwks(),
            audience=settings.auth0_audience,
            issuer=settings.issuer,
        )
    except JWTError as primary_error:
        # 2. Additive fallback: the separate Hollisworks tenant, only when set.
        if settings.hollisworks_enabled:
            try:
                return _decode_against(
                    token,
                    get_hollisworks_jwks(),
                    audience=settings.hollisworks_auth0_audience,
                    issuer=settings.hollisworks_issuer,
                )
            except JWTError:
                pass
        else:
            # Not configured. If the token is not 2nd Act's, name the gap
            # instead of re-raising 2nd Act's misleading key-lookup error.
            issuer = _unverified_issuer(token)
            if issuer and issuer != settings.issuer:
                message = (
                    f"Token issuer {issuer!r} is not the 2nd Act tenant and no "
                    f"second Auth0 tenant is configured on this API — "
                    f"HOLLISWORKS_AUTH0_DOMAIN is unset. Set it (and "
                    f"HOLLISWORKS_AUTH0_AUDIENCE) in the API service's "
                    f"environment; see render.yaml."
                )
                print(f"[auth] {message}")
                raise JWTError(message) from primary_error
        raise primary_error


app = FastAPI(title="Ripasso API", version=API_VERSION)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in get_settings().allowed_origins.split(",") if o.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# The detail string a deactivated account's requests are rejected with. Named so
# the frontend can recognise this specific 403 (an ordinary permission failure
# needs a different message and a different remedy) and so the verify script
# asserts the real value rather than a substring it invented.
ACCOUNT_DEACTIVATED_DETAIL = (
    "This account has been deactivated. Contact your administrator."
)


async def _resolve_account_state(request: Request) -> tuple[bool, bool]:
    """``(is_super_admin, is_active)`` for the caller — ONE read, READ-ONLY.

    Reads ``users.role`` and ``users.is_active`` by ``auth0_sub`` and reuses
    ``services.rbac.is_super_admin``. Unlike ``ensure_user`` it never inserts — a
    brand-new user simply resolves to not-super, which is the safe default.

    ``is_active`` defaults to True when NO row exists: a first-ever request has
    nothing to be deactivated yet, and ``ensure_user`` is about to create the
    row (``users.is_active`` is ``NOT NULL DEFAULT true``). Denying here would
    lock out every new member.

    Future note: once the app connects as the non-bypass ``app_service`` role,
    this read is itself subject to RLS on ``users`` (RLS enabled, and as of this
    sprint NO policy) — so it will return nothing and every caller is treated as
    not-super. That fails safe (deny) and is exactly the ``users`` carve-out a
    later sprint must design (see ``services.users.ensure_user``).
    """
    claims = getattr(request.state, "user", None) or {}
    sub = claims.get("sub")
    if not sub:
        return False, True

    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT role, is_active FROM users WHERE auth0_sub = $1", sub
        )

    # Hollisworks-tenant identity IS platform staff — recognized directly from the
    # validated token issuer, before (and independent of) any users-row read, so a
    # first request establishes Super Admin context without a write race.
    #
    # Deliberately does NOT exempt staff from the is_active gate: the Super Admin
    # escape hatch is about authorisation, and a deactivated account is not an
    # authorisation question — it is a closed account. A platform admin who
    # deactivates themselves is locked out, same as anyone else, and is
    # reactivated by another admin (or by SQL). Making staff the one identity
    # that cannot be switched off would be the more dangerous default.
    is_super = is_hollisworks_claims(claims) or (
        is_super_admin(dict(row)) if row else False
    )
    is_active = bool(row["is_active"]) if row else True
    return is_super, is_active


async def _resolve_is_super_admin(request: Request) -> bool:
    """Back-compat wrapper — the super-admin half of :func:`_resolve_account_state`."""
    is_super, _ = await _resolve_account_state(request)
    return is_super


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
            is_super, is_active = await _resolve_account_state(request)
        except Exception as exc:
            print(f"[rls] account state resolution failed (default False/active): {exc}")
            is_super, is_active = False, True

        # THE ACTIVE-ACCOUNT GATE (user-management sprint, Task 5).
        #
        # This is the real session check point, and it is here for a reason: it
        # is the ONE place every authenticated request already passes through
        # AFTER the token is validated and BEFORE any route handler runs, and it
        # already performs exactly the ``users``-by-``auth0_sub`` read the gate
        # needs — so enforcement costs zero extra queries and cannot be missed by
        # a new endpoint that forgets to opt in.
        #
        # It could NOT go in the Auth0 layer: Auth0 issued the token and knows
        # nothing about ``users.is_active``, and a token already in a browser
        # stays valid for its full lifetime, so revoking access has to happen on
        # OUR side of the boundary. It could not go in ``ensure_user`` either —
        # that function's contract is "never raises", because every read path
        # depends on it.
        #
        # Deliberately 403 and not 401: the credential is valid, the account is
        # closed. A 401 would send the frontend into a re-login loop that
        # re-presents the same working token.
        if not is_active:
            reset_auth0_sub_context(sub_token)
            return JSONResponse(
                status_code=403, content={"detail": ACCOUNT_DEACTIVATED_DETAIL}
            )

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
app.include_router(documents_router, prefix="/api/v1")
app.include_router(document_links_router, prefix="/api/v1")
app.include_router(document_review_router, prefix="/api/v1")
app.include_router(semantic_search_router, prefix="/api/v1")
app.include_router(vdr_router, prefix="/api/v1")
app.include_router(investment_profile_router, prefix="/api/v1")
app.include_router(marketing_router, prefix="/api/v1")
app.include_router(marketplace_router, prefix="/api/v1")
app.include_router(custody_import_router, prefix="/api/v1")
app.include_router(portfolio_router, prefix="/api/v1")
app.include_router(portfolio_ingest_router, prefix="/api/v1")
app.include_router(portfolio_positions_router, prefix="/api/v1")
app.include_router(portfolio_securities_router, prefix="/api/v1")
app.include_router(portfolio_transactions_router, prefix="/api/v1")
app.include_router(spv_router, prefix="/api/v1")
app.include_router(entity_graph_router, prefix="/api/v1")
app.include_router(ownership_tree_router, prefix="/api/v1")
app.include_router(reference_router, prefix="/api/v1")
app.include_router(notifications_router, prefix="/api/v1")
app.include_router(admin_router, prefix="/api/v1")
app.include_router(enroll_router, prefix="/api/v1")
app.include_router(invites_router, prefix="/api/v1")
app.include_router(staff_assignments_router, prefix="/api/v1")
app.include_router(households_router, prefix="/api/v1")
app.include_router(billing_groups_router, prefix="/api/v1")
app.include_router(fee_chat_router, prefix="/api/v1")
app.include_router(fee_schedules_router, prefix="/api/v1")
app.include_router(profitability_router, prefix="/api/v1")
app.include_router(users_router, prefix="/api/v1")
app.include_router(allocation_lens_router, prefix="/api/v1")
app.include_router(ledger_router, prefix="/api/v1")
app.include_router(org_settings_router, prefix="/api/v1")
app.include_router(tenant_router, prefix="/api/v1")
app.include_router(restricted_access_router, prefix="/api/v1")
app.include_router(pricing_admin_router, prefix="/api/v1")
app.include_router(pricing_surface_router, prefix="/api/v1")
app.include_router(trading_authority_router, prefix="/api/v1")
app.include_router(profiles_router, prefix="/api/v1")
app.include_router(workflows_router, prefix="/api/v1")
app.include_router(udf_router, prefix="/api/v1")
app.include_router(modeling_ta_router, prefix="/api/v1")
# Debug router mounted at root so the path is exactly /debug/user-info.
app.include_router(debug_router)
