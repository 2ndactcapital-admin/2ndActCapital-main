"""Ripasso API.

Ripasso is the licensable platform; each client firm is a tenant org whose
branding lives in ``org_settings`` (Sprint 24). Nothing here names a specific
client.

FastAPI application entrypoint. Exposes a public health check and protects
every other route with Auth0-issued JWT validation.
"""

from functools import lru_cache

import httpx
from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from jose import jwt
from jose.exceptions import JWTError
from pydantic_settings import BaseSettings, SettingsConfigDict

from routers.admin import router as admin_router
from routers.allocation_lens import router as allocation_lens_router
from routers.altruist_connection import router as altruist_connection_router
from routers.altruist_webhook import router as altruist_webhook_router
from routers.ledger import router as ledger_router
from routers.assistant import router as assistant_router
from routers.custody_import import router as custody_import_router
from routers.dashboard import router as dashboard_router
from routers.debug import router as debug_router
from routers.entities import router as entities_router, get_org_id
from routers.modeling_ta import router as modeling_ta_router
from routers.entity_graph import router as entity_graph_router
from routers.crm_detail import router as crm_detail_router  # CRMDETAIL1
from routers.portentry import router as portentry_router  # PORTENTRY1
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
from routers.market_data import router as market_data_router
from routers.market_data_personal import router as market_data_personal_router
from routers.replication import router as replication_router
from routers.replication_models import router as replication_models_router
from routers.replication_options import router as replication_options_router
from routers.security_data import router as security_data_router
from routers.security_master import router as security_master_router  # SECMASTER1
from routers.ai_drift import router as ai_drift_router  # S28 — AI drift monitor
from routers.blocks import router as blocks_router
from routers.replication_rep10 import router as replication_rep10_router
from routers.rollup2 import router as rollup2_router
from routers.replication_notes import router as replication_notes_router
from routers.replication_note_checks import router as replication_note_checks_router
from routers.tax_gifts import router as tax_gifts_router
from routers.execution import router as execution_router
from routers.account_models import router as account_models_router
from routers.long_short import router as long_short_router  # REP13
from routers.disclosures import router as disclosures_router  # DISCLOSURES1
from routers.section351 import router as section351_router  # REP14
from routers.model_provider import router as model_provider_router  # REP15
from routers.workflow_templates import router as workflow_templates_router  # WFTEMPLATES1
from routers.sod import router as sod_router  # S30 — segregation of duties
from routers.workflow_autonomy import router as workflow_autonomy_router  # S29B — unattended workflow writes
from routers.cash import router as cash_router  # CASH01 — Household → Treasury: obligations + coverage
from routers.payments import router as payments_router  # CASH07 — Treasury → Payments (stage A, manual)
from routers.cash_payees import router as cash_payees_router  # CASH08 — Treasury → Payees / Invoices; third-party switch
from routers.cash_rules import router as cash_rules_router  # CASH06 — Treasury → Rules (stage A: drafts only)
from routers.tax import router as tax_router
from routers.held_away import router as held_away_router
from routers.xray import router as xray_router
from routers.authority import router as authority_router
from routers.approvals import router as approvals_router  # BULKAPPROVE1
from routers.lot_method import router as lot_method_router
from routers.custodians import router as custodians_router
from routers.human_tasks import router as human_tasks_router
from routers.models import router as models_router
from routers.marketplace import router as marketplace_router
from routers.deal_room import router as deal_room_router
from routers.notifications import router as notifications_router
from routers.org_settings import router as org_settings_router
from routers.ai_ensembles import router as ai_ensembles_router
from routers.model_research import router as model_research_router
from routers.note_extraction_admin import router as note_extraction_admin_router
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
from routers.edgar_pipeline_admin import router as edgar_pipeline_admin_router
from routers.pricing_surface import router as pricing_surface_router
from routers.spv import router as spv_router
from routers.staff_assignments import router as staff_assignments_router
from routers.staff_visibility import router as staff_visibility_router  # VISIBILITY1
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
from services.rbac import is_super_admin  # noqa: F401  (re-exported for importers)

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
    # Altruist Realtime API webhook receiver (Altruist Sprint 6) — pre-auth by
    # necessity, unlike every path above: the caller is Altruist itself, with
    # no Auth0 session, so it is NOT unsigned like the rest of this set — it
    # is gated by its own HMAC signature check inside routers/altruist_webhook
    # BEFORE any payload parsing or DB write. See that router's module
    # docstring for why this is the first genuinely signature-verified
    # unauthenticated route in this app.
    "/api/v1/altruist/webhook",
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

    Decides the ORG a token belongs to (``routers.entities.org_id_from_claims``)
    and which ``/userinfo`` to ask. It is NOT a Super Admin signal (AUTHZ1):
    super admin comes only from an admitted ``users`` row, and a new
    Hollisworks login becomes one only when its verified email is in
    ``PLATFORM_STAFF_EMAILS`` (``services.users``). Returns False when
    Hollisworks is not configured.
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


async def _family_path_guard(request: Request) -> None:
    """VISIBILITY1 — the ONE family-visibility guard, on every route: a restricted staff caller of an org that
    enforces staff visibility gets the same 404 for another family's record (path or query id) as for a missing
    one, on reads and writes alike. A no-op (no statement) for everyone else. services.family_visibility."""
    from services.family_visibility import family_path_guard

    await family_path_guard(request)


app = FastAPI(title="Ripasso API", version=API_VERSION, dependencies=[Depends(_family_path_guard)])

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


# AUTHZ1: paths an authenticated caller may reach WITHOUT an admitted account.
# /users/me lets a pending login see its own status (the front end renders the
# "Access pending" page from it). /enroll/accept is where an invitee CLAIMS
# their pre-created row — provisioning must not run first, or the new login
# would get a pending row of its own and the claim would hit sub_conflict.
# USERMGMT1: /users/me/contact-email lets a pending login with no real email
# leave an address for the admins (its own row only; never used for linking).
PENDING_ALLOWED_PATHS = {"/api/v1/users/me", "/api/v1/users/me/contact-email"}
PROVISIONING_EXEMPT_PATHS = {"/api/v1/enroll/accept"}


def _is_public(path: str) -> bool:
    return path in PUBLIC_PATHS or path.startswith("/debug/")


async def _resolve_account_state(request: Request) -> tuple[bool, bool]:
    """``(is_super_admin, is_active)`` for the caller, from the DB Principal.

    Back-compat for callers of the pre-AUTHZ1 helper. The token issuer is NOT
    a super-admin signal any more — only an admitted ``users`` row with
    ``role = 'super_admin'`` is (``services.authz``).
    """
    from services.authz import principal_of

    p = principal_of(request)
    if p is None:
        return False, True
    return p.is_super_admin, p.is_active


async def _resolve_is_super_admin(request: Request) -> bool:
    """Back-compat wrapper — the super-admin half of :func:`_resolve_account_state`."""
    is_super, _ = await _resolve_account_state(request)
    return is_super


# NOTE ON ORDERING: this middleware is defined BEFORE auth0_jwt_middleware on
# purpose. Starlette runs the LAST-registered middleware outermost, so defining
# this one first makes it *inner* — it therefore runs AFTER auth0_jwt_middleware
# has populated request.state.user, which we need to resolve org_id.
async def _scrub_family_rows(request: Request, response):
    """VISIBILITY1 — re-emit a 2xx JSON response with every list item naming an invisible family removed. A body
    that cannot be read as JSON is refused (fail closed: 500), never passed through unscrubbed."""
    import json as _json

    from services.database import get_pool
    from services.family_visibility import scope_for_request, scrub

    ctype = response.headers.get("content-type", "")
    if not (200 <= response.status_code < 300) or "application/json" not in ctype:
        return response
    body = b""
    async for chunk in response.body_iterator:
        body += chunk if isinstance(chunk, bytes) else chunk.encode()
    try:
        scope = await scope_for_request(request, await get_pool())
        payload = _json.loads(body) if body else None
        if scope is not None and payload is not None:
            payload = scrub(payload, scope)
    except Exception as exc:
        print(f"[visibility] scrub failed (refused): {exc}")
        return JSONResponse(status_code=500, content={"detail": "Visibility could not be decided"})
    headers = {k: v for k, v in response.headers.items() if k.lower() not in ("content-length", "content-type")}
    return JSONResponse(status_code=response.status_code, content=payload, headers=headers)


@app.middleware("http")
async def rls_context_middleware(request: Request, call_next):
    """Resolve the ONE Principal for the request, gate on it, and set the RLS
    ContextVars (org_id + is_super_admin) for every query the handler runs.

    Sequence (AUTHZ1):
      1. ``app.current_auth0_sub`` FIRST — the users/user_identities bootstrap
         leg is what lets the identity read succeed before anything else is
         known.
      2. org_id from ``get_org_id`` (UNCHANGED: the issuing tenant decides the
         org; org isolation is not this sprint's business).
      3. Resolve + provision the caller under that org context
         (``services.users.resolve_request_principal``) and cache the
         Principal on ``request.state.principal`` — the single DB load every
         gate in every router then reads.
      4. Gate: no resolvable user → 401; deactivated → 403; not admitted
         (pending / retired / rejected) → 403 except PENDING_ALLOWED_PATHS.
      5. RLS context for the handler: is_super_admin from the Principal only.

    Public paths carry no claims and skip 3–4 entirely.
    """
    from services.authz import (
        ACCESS_PENDING_DETAIL,
        ACCOUNT_RETIRED_DETAIL,
        NO_USER_DETAIL,
        STATUS_PENDING,
    )
    from services.users import resolve_request_principal

    claims = getattr(request.state, "user", None) or {}
    sub = claims.get("sub")
    path = request.url.path

    sub_token = set_auth0_sub_context(sub)

    org_id = None
    is_super = False
    if claims and not _is_public(path) and request.method != "OPTIONS":
        try:
            org_id = get_org_id(request)
        except Exception as exc:  # never block the request on context resolution
            print(f"[rls] org_id resolution failed (default-deny): {exc}")
            org_id = None

        principal = None
        if path not in PROVISIONING_EXEMPT_PATHS:
            resolve_tokens = set_rls_context(org_id, False)
            try:
                principal = await resolve_request_principal(request)
            except Exception as exc:
                print(f"[rls] principal resolution failed (deny): {exc}")
                principal = None
            finally:
                reset_rls_context(resolve_tokens)

            def _deny(code: int, detail: str, **extra):
                reset_auth0_sub_context(sub_token)
                return JSONResponse(status_code=code, content={"detail": detail, **extra})

            if principal is None:
                return _deny(401, NO_USER_DETAIL)
            # THE ACTIVE-ACCOUNT GATE (user-management sprint): 403 not 401 —
            # the credential is valid, the account is closed; a 401 would send
            # the frontend into a re-login loop with the same working token.
            if not principal.is_active:
                return _deny(403, ACCOUNT_DEACTIVATED_DETAIL)
            if not principal.admitted and path not in PENDING_ALLOWED_PATHS:
                if principal.status == STATUS_PENDING:
                    return _deny(403, ACCESS_PENDING_DETAIL, status=principal.status)
                return _deny(403, ACCOUNT_RETIRED_DETAIL, status=principal.status)
            is_super = principal.is_super_admin

    tokens = set_rls_context(org_id, is_super)
    try:
        response = await call_next(request)
        # VISIBILITY1 — a restricted staff caller of an enforcing org never receives another family's row in any
        # JSON list (services.family_visibility.scrub). No-op for everyone else.
        from services.authz import principal_of
        from services.family_visibility import restricted_principal

        if restricted_principal(principal_of(request)):
            response = await _scrub_family_rows(request, response)
        return response
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
    from services import authz

    if authz.TEST_MODE:
        print(
            "[authz] WARNING: AUTHZ_TEST_MODE=1 is set — the UUID-sub stub and "
            "claim-based test grants are ENABLED. This must never be set in "
            "production."
        )
    from services.assistant_actions import register_all
    from services.action_registry import REGISTRY
    from services.brief_blocks import register_brief_blocks
    from services.database import get_pool, reset_rls_context, set_rls_context

    register_all()
    register_brief_blocks()
    seed_org_id = "00000000-0000-0000-0000-000000000001"
    try:
        pool = await get_pool()
        # RLS is enforced now (app_service, not a bypass role) — this write's
        # own org_id already matches seed_org_id, so scoping the RLS context
        # to that org (not is_super_admin) is the minimal, correct fix. Found
        # by the rlscutover sprint's own smoke test: under postgres-bypass
        # this insert always silently succeeded with no context set at all,
        # so the gap was invisible until app_service actually enforced it.
        tokens = set_rls_context(seed_org_id, False)
        try:
            await REGISTRY.sync_catalog(pool, seed_org_id)
        finally:
            reset_rls_context(tokens)
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
app.include_router(deal_room_router, prefix="/api/v1")
app.include_router(custody_import_router, prefix="/api/v1")
app.include_router(altruist_connection_router, prefix="/api/v1")
app.include_router(altruist_webhook_router, prefix="/api/v1")
app.include_router(portfolio_router, prefix="/api/v1")
app.include_router(portfolio_ingest_router, prefix="/api/v1")
app.include_router(portfolio_positions_router, prefix="/api/v1")
app.include_router(portfolio_securities_router, prefix="/api/v1")
app.include_router(portfolio_transactions_router, prefix="/api/v1")
app.include_router(spv_router, prefix="/api/v1")
app.include_router(entity_graph_router, prefix="/api/v1")
app.include_router(crm_detail_router, prefix="/api/v1")  # CRMDETAIL1
app.include_router(portentry_router, prefix="/api/v1")  # PORTENTRY1
app.include_router(ownership_tree_router, prefix="/api/v1")
app.include_router(reference_router, prefix="/api/v1")
app.include_router(notifications_router, prefix="/api/v1")
app.include_router(admin_router, prefix="/api/v1")
app.include_router(enroll_router, prefix="/api/v1")
app.include_router(invites_router, prefix="/api/v1")
app.include_router(staff_assignments_router, prefix="/api/v1")
app.include_router(staff_visibility_router, prefix="/api/v1")  # VISIBILITY1
app.include_router(households_router, prefix="/api/v1")
app.include_router(billing_groups_router, prefix="/api/v1")
app.include_router(fee_chat_router, prefix="/api/v1")
app.include_router(fee_schedules_router, prefix="/api/v1")
app.include_router(profitability_router, prefix="/api/v1")
app.include_router(users_router, prefix="/api/v1")
app.include_router(allocation_lens_router, prefix="/api/v1")
app.include_router(ledger_router, prefix="/api/v1")
app.include_router(org_settings_router, prefix="/api/v1")
app.include_router(ai_ensembles_router, prefix="/api/v1")
app.include_router(model_research_router, prefix="/api/v1")
app.include_router(note_extraction_admin_router, prefix="/api/v1")
app.include_router(tenant_router, prefix="/api/v1")
app.include_router(restricted_access_router, prefix="/api/v1")
app.include_router(pricing_admin_router, prefix="/api/v1")
app.include_router(edgar_pipeline_admin_router, prefix="/api/v1")
app.include_router(pricing_surface_router, prefix="/api/v1")
app.include_router(trading_authority_router, prefix="/api/v1")
app.include_router(profiles_router, prefix="/api/v1")
app.include_router(workflows_router, prefix="/api/v1")
app.include_router(udf_router, prefix="/api/v1")
app.include_router(modeling_ta_router, prefix="/api/v1")
app.include_router(market_data_router, prefix="/api/v1")
app.include_router(market_data_personal_router, prefix="/api/v1")
app.include_router(replication_router, prefix="/api/v1")
app.include_router(replication_models_router, prefix="/api/v1")
app.include_router(replication_options_router, prefix="/api/v1")
app.include_router(security_data_router, prefix="/api/v1")
app.include_router(security_master_router, prefix="/api/v1")    # SECMASTER1 — security master (Super Admin)
app.include_router(ai_drift_router, prefix="/api/v1")           # S28 — AI drift monitor (manage_org_settings / Super Admin)
app.include_router(execution_router, prefix="/api/v1")
app.include_router(blocks_router, prefix="/api/v1")    # rep9 — block trading
app.include_router(replication_rep10_router, prefix="/api/v1")    # rep10 — tilts, completion, backtests
app.include_router(rollup2_router, prefix="/api/v1")    # ROLLUP2 — duplicate ownership report, fund category map
# rep12 — new-issue note checks + the underlying queue. BEFORE rep11: its /replication/notes/{note_id} would
# otherwise swallow /replication/notes/checks and /replication/notes/underlyings (a 422, never a fall-through).
app.include_router(replication_note_checks_router, prefix="/api/v1")
app.include_router(replication_notes_router, prefix="/api/v1")    # rep11 — structured notes
app.include_router(tax_gifts_router, prefix="/api/v1")    # rep10 — charitable lot gifting
app.include_router(account_models_router, prefix="/api/v1")
app.include_router(long_short_router, prefix="/api/v1")  # REP13 — the long/short extension
app.include_router(disclosures_router, prefix="/api/v1")  # DISCLOSURES1 — the disclosure library
app.include_router(section351_router, prefix="/api/v1")  # REP14 — the Section 351 exit
app.include_router(model_provider_router, prefix="/api/v1")  # REP15 — platform-published models
app.include_router(workflow_templates_router, prefix="/api/v1")  # WFTEMPLATES1 — workflow template library
app.include_router(sod_router, prefix="/api/v1")  # S30 — segregation of duties (Admin → Segregation of duties)
app.include_router(workflow_autonomy_router, prefix="/api/v1")  # S29B — Admin → Workflows → Autonomy
app.include_router(cash_router, prefix="/api/v1")  # CASH01 — obligations ledger + cash coverage
app.include_router(payments_router, prefix="/api/v1")  # CASH07 — payment instructions (same owner, manual)
app.include_router(cash_payees_router, prefix="/api/v1")  # CASH08 — payees, bank changes, SLOAs, invoices (no 3rd party)
app.include_router(cash_rules_router, prefix="/api/v1")  # CASH06 — cash rules (draft payment instructions)
app.include_router(tax_router, prefix="/api/v1")
app.include_router(held_away_router, prefix="/api/v1")
app.include_router(xray_router, prefix="/api/v1")
app.include_router(authority_router, prefix="/api/v1")
app.include_router(approvals_router, prefix="/api/v1")  # BULKAPPROVE1
app.include_router(lot_method_router, prefix="/api/v1")
app.include_router(custodians_router, prefix="/api/v1")
app.include_router(human_tasks_router, prefix="/api/v1")
app.include_router(models_router, prefix="/api/v1")
# Debug router mounted at root so the path is exactly /debug/user-info.
app.include_router(debug_router)
