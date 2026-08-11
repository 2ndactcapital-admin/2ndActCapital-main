/**
 * Pure, dependency-free Host → Auth0 tenant resolution.
 *
 * This module deliberately imports NOTHING — no Auth0 SDK, no "@/..." aliases —
 * so the EXACT rule the deployed login route and middleware use can be exercised
 * by a plain Node harness (see apps/api/scripts/authhostconfig_harness.mjs). That
 * hermetic testability is the whole point: Bug 2 slipped through because the
 * isolated test set HOLLISWORKS_AUTH0_* env vars and only grepped source strings,
 * so it never observed what the real Auth0 SDK does when those vars are ABSENT.
 *
 * Bug 2 (root cause): `admin.hollisworks.com` must authenticate against the
 * SEPARATE Hollisworks Auth0 tenant. `lib/auth0Hollisworks.js` builds its client
 * with `domain: process.env.HOLLISWORKS_AUTH0_DOMAIN`. When that env var is
 * missing (as it was in production), the Auth0 SDK's constructor silently falls
 * back — `domain: options.domain ?? process.env.AUTH0_DOMAIN` — to 2nd Act's
 * tenant (dev-smmrfubsfscif3t1). So the admin host initiated login against 2nd
 * Act's tenant and then failed callback with "the state parameter is invalid".
 *
 * The fix: resolve the admin host's tenant config HERE and FAIL LOUD when the
 * Hollisworks vars are missing, so we NEVER silently fall back to 2nd Act's
 * tenant. Every other host keeps using the existing 2nd Act client, unchanged.
 */

export const HOLLISWORKS_ADMIN_HOST = "admin.hollisworks.com";

// Real Auth0 tenant issuer hints — used for assertions/telemetry only. The
// authoritative domains come from env (below); these let a test prove which
// tenant a host resolved to without hardcoding the full domain in app code.
export const TWOACT_ISSUER_HINT = "dev-smmrfubsfscif3t1";
export const HOLLISWORKS_ISSUER_HINT = "dev-gy85vzuf6mruzv3j";

/** Normalize a Host header to a bare lowercase hostname (strip :port, trailing dot). */
export function bareHost(host) {
  if (!host) return "";
  return String(host).split(":")[0].trim().toLowerCase().replace(/\.$/, "");
}

/** True when the request's Host is exactly the Hollisworks admin surface. */
export function isHollisworksAdminHost(host) {
  return bareHost(host) === HOLLISWORKS_ADMIN_HOST;
}

/** Raised when the admin host is reached but the Hollisworks tenant is unconfigured. */
export class HollisworksAuthConfigError extends Error {
  constructor(missing) {
    super(
      `admin.hollisworks.com Auth0 is not configured — missing ${missing.join(", ")}. ` +
        `Refusing to fall back to the 2nd Act tenant. Set the HOLLISWORKS_AUTH0_* ` +
        `environment variables (Vercel Production + Preview) for the Hollisworks tenant.`
    );
    this.name = "HollisworksAuthConfigError";
    this.missing = missing;
  }
}

/**
 * Resolve which Auth0 tenant a given Host authenticates against, and the
 * EFFECTIVE credentials the client must be built with.
 *
 * `admin.hollisworks.com` → the Hollisworks tenant. Its domain/clientId/secret
 * are REQUIRED; if any is missing this THROWS rather than letting the SDK fall
 * back to `AUTH0_DOMAIN` (2nd Act). Every other host → the existing 2nd Act
 * tenant (`AUTH0_*`), provably unchanged.
 *
 * @param {string} host  the request Host header
 * @param {object} env   defaults to process.env
 */
export function resolveAuthTenantForHost(host, env = process.env) {
  if (isHollisworksAdminHost(host)) {
    const domain = env.HOLLISWORKS_AUTH0_DOMAIN;
    const clientId = env.HOLLISWORKS_AUTH0_CLIENT_ID;
    const clientSecret = env.HOLLISWORKS_AUTH0_CLIENT_SECRET;
    const missing = [];
    if (!domain) missing.push("HOLLISWORKS_AUTH0_DOMAIN");
    if (!clientId) missing.push("HOLLISWORKS_AUTH0_CLIENT_ID");
    if (!clientSecret) missing.push("HOLLISWORKS_AUTH0_CLIENT_SECRET");
    if (missing.length) {
      // FAIL LOUD — do NOT return 2nd Act's config here.
      throw new HollisworksAuthConfigError(missing);
    }
    return {
      tenant: "hollisworks",
      domain,
      clientId,
      clientSecret,
      // Cookie-encryption secret may share the 2nd Act secret in dev.
      secret: env.HOLLISWORKS_AUTH0_SECRET || env.AUTH0_SECRET,
      audience:
        env.HOLLISWORKS_AUTH0_AUDIENCE || "https://api.2ndactcapital.com",
    };
  }
  // Every other host = the existing 2nd Act tenant, exactly as before.
  return {
    tenant: "2ndact",
    domain: env.AUTH0_DOMAIN,
    clientId: env.AUTH0_CLIENT_ID,
    clientSecret: env.AUTH0_CLIENT_SECRET,
    secret: env.AUTH0_SECRET,
    audience: "https://api.2ndactcapital.com",
  };
}
