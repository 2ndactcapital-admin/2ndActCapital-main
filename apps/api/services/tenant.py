"""Host-header tenant resolution + DNS-safe slug validation.

Headless Multi-Tenant — Sprint 1. This module is the single source of truth for:

  * ``validate_slug``   — an org slug is about to become a LIVE public subdomain
    (``<slug>.ripasso.com``), so it must be genuinely DNS-safe and must not
    collide with a reserved platform host. Used by POST /orgs.
  * ``extract_subdomain`` / ``resolve_tenant`` — given a request's Host header,
    resolve which org the request is for, falling back to the default org
    (2nd Act's own operation) for the bare/default domain, an unknown
    subdomain, or a malformed Host. NEVER raises.

PRE-AUTH SAFETY (the real Task-1a finding): the org lookup runs before anyone
is authenticated, so it has NO org context. Under the non-bypass ``app_service``
role the base ``organizations_self_or_super`` policy returns zero rows for such
a request; the ``organizations_preauth_resolve`` SELECT carve-out
(docs/multitenant1_part1.sql) is what makes this lookup return the row safely.
We reuse the SAME shared RLS-aware pool (services.database.get_pool) that
/theme/public uses — no separate bypass connection is opened here.
"""

import re

# The default org — 2nd Act Capital's own tenant. The bare/default domain, an
# unrecognized subdomain, or a malformed Host all fall back to this, so 2nd
# Act's own operation is completely unchanged by tenant resolution.
from routers.entities import DEFAULT_ORG_ID

# A DNS label: lowercase alphanumerics in hyphen-separated groups. This forbids
# leading/trailing/double hyphens, uppercase, underscores, dots and any other
# character. Case is NOT coerced — an uppercase slug is REJECTED, not silently
# lowercased, because the caller must supply exactly the subdomain they will get.
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

SLUG_MIN_LEN = 2
SLUG_MAX_LEN = 63  # a single DNS label may not exceed 63 octets

# Subdomains that would collide with real platform / infrastructure hosts, or
# with 2nd Act's / Ripasso's own bare hosts. A new tenant may not claim any of
# these because the slug becomes a literal, live subdomain.
RESERVED_SLUGS = frozenset({
    # explicitly required by the sprint
    "www", "api", "admin", "app", "mail",
    # 2nd Act / Ripasso own hosts
    "2ndact", "2ndactcapital", "ripasso", "ripassocapital",
    # common mail / DNS / infra hosts
    "mx", "smtp", "imap", "pop", "pop3", "webmail", "email", "ns", "ns1",
    "ns2", "dns", "ftp", "sftp", "vpn", "proxy", "gateway",
    "postmaster", "hostmaster", "noreply", "no-reply",
    # platform-facing hosts / route collisions
    "auth", "sso", "saml", "login", "logout", "signup", "signin", "register",
    "account", "accounts", "dashboard", "portal", "console", "billing",
    "support", "help", "helpdesk", "status", "docs", "blog", "cdn", "static",
    "assets", "media", "files", "img", "images", "download", "downloads",
    "public", "internal", "system", "sys", "root", "test", "demo", "dev",
    "staging", "stage", "prod", "production", "sandbox", "beta", "alpha",
    "ci", "git", "graphql", "webhook", "webhooks", "callback", "oauth",
})


class SlugValidationError(ValueError):
    """A proposed org slug is not DNS-safe or is reserved."""


def validate_slug(slug: str) -> None:
    """Raise :class:`SlugValidationError` unless ``slug`` is a safe org slug.

    A slug is safe when it is a single DNS label — lowercase letters, digits and
    single interior hyphens only — within the length bounds and not a reserved
    platform host. Callers should pass the raw (stripped) user input; uppercase
    and special characters are rejected rather than coerced.
    """
    if not isinstance(slug, str):
        raise SlugValidationError("Slug is required")
    if slug != slug.strip():
        raise SlugValidationError("Slug must not have leading or trailing whitespace")
    if not slug:
        raise SlugValidationError("Slug is required")
    if len(slug) < SLUG_MIN_LEN:
        raise SlugValidationError(
            f"Slug must be at least {SLUG_MIN_LEN} characters"
        )
    if len(slug) > SLUG_MAX_LEN:
        raise SlugValidationError(
            f"Slug must be at most {SLUG_MAX_LEN} characters"
        )
    if not SLUG_RE.match(slug):
        raise SlugValidationError(
            "Slug must be lowercase letters, digits and single hyphens only "
            "(no leading, trailing or double hyphens, no uppercase, spaces or "
            "special characters)"
        )
    if slug in RESERVED_SLUGS:
        raise SlugValidationError(
            f"Slug '{slug}' is reserved and cannot be used as a subdomain"
        )


def extract_subdomain(host) -> str | None:
    """Extract the tenant subdomain label from a Host header, or ``None``.

    Returns ``None`` — meaning "use the default org" — for the bare/default
    domain (``ripasso.com``, ``2ndactcapital.com``), for ``www``, for
    localhost / bare IPs / single-label hosts, and for anything malformed. This
    function NEVER raises; a garbage Host simply yields ``None``.

    ``myria.ripasso.com`` -> ``"myria"``; ``ripasso.com`` -> ``None``.
    """
    if not host or not isinstance(host, str):
        return None
    try:
        h = host.strip().lower()
        if not h:
            return None
        # An IPv6 literal (``[::1]`` / ``[::1]:8000``) has no subdomain.
        if h.startswith("[") or "]" in h:
            return None
        # Drop the port, if any, then any trailing dot (FQDN form).
        h = h.split(":", 1)[0].rstrip(".")
        if not h:
            return None
        parts = h.split(".")
        # Bare IPv4 address -> no subdomain.
        if len(parts) == 4 and all(p.isdigit() for p in parts):
            return None
        # Need at least <sub>.<domain>.<tld> for a subdomain to exist. This
        # keeps the apex (ripasso.com, 2ndactcapital.com) on the default org.
        if len(parts) < 3:
            return None
        sub = parts[0]
        # ``www`` is the bare marketing site, not a tenant.
        if not sub or sub == "www":
            return None
        return sub
    except Exception:
        # Defensive: resolution must never crash on an unexpected Host.
        return None


async def resolve_tenant(conn, host) -> dict:
    """Resolve a request's Host header to a tenant org, pre-auth-safe.

    Headless Multi-Tenant Sprint 1 CORRECTION (Hollisworks marketing): the
    bare/default host is NOT a tenant. Hollisworks is the licensable *platform*;
    the apex ``hollisworks.com`` / ``www.hollisworks.com`` is the platform's own
    marketing site, and each client firm (2nd Act included) is reached ONLY via
    its own ``<slug>.hollisworks.com`` subdomain. So the no-subdomain case now
    signals "show the Hollisworks marketing page" rather than silently resolving
    to 2nd Act's :data:`DEFAULT_ORG_ID` as if that were a real tenant match.

    Returns a dict with:
      * ``org_id``   — the resolved tenant's id, or ``None`` in the marketing
        case (deliberately NOT DEFAULT_ORG_ID — there is no real tenant).
      * ``org_slug`` / ``org_name`` — populated only for a resolved tenant.
      * ``subdomain`` — the extracted label (``None`` for apex/www/malformed).
      * ``source``   — ``"subdomain"`` for a resolved tenant, else ``"platform"``.
      * ``resolved`` — True only when a matching org was found for the subdomain.
      * ``marketing``— True whenever there is no resolved tenant, i.e. the
        Hollisworks marketing page should be served. ``marketing == not resolved``.

    NEVER raises: any error, an apex host, an unknown subdomain, or a malformed
    Host all yield the marketing case. 2nd Act's own app is unchanged — it simply
    resolves through its ``2ndactcapital.hollisworks.com`` subdomain like any
    other tenant.

    ``conn`` must come from the shared RLS-aware pool; the pre-auth read relies
    on the ``organizations_preauth_resolve`` carve-out under app_service.
    """
    result = {
        "org_id": None,
        "org_slug": None,
        "org_name": None,
        "subdomain": None,
        "source": "platform",
        "resolved": False,
        "marketing": True,
    }
    try:
        sub = extract_subdomain(host)
        result["subdomain"] = sub
        if sub:
            row = await conn.fetchrow(
                "SELECT id, name, slug FROM organizations WHERE slug = $1", sub
            )
            if row is not None:
                result.update(
                    org_id=str(row["id"]),
                    org_slug=row["slug"],
                    org_name=row["name"],
                    source="subdomain",
                    resolved=True,
                    marketing=False,
                )
                return result
        # No subdomain (apex/www), an unknown subdomain, or a malformed Host:
        # this is the platform's own marketing surface, NOT a tenant. Leave
        # org_id None + marketing True so the frontend serves the Hollisworks
        # page instead of mistaking the request for 2nd Act's app.
    except Exception as exc:  # never block a request on tenant resolution
        print(f"[tenant] resolve failed, serving platform marketing: {exc}")
    return result
