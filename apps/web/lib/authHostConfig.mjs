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

/**
 * The public base URL the Auth0 SDK must build `redirect_uri` from for the
 * Hollisworks admin surface — derived from the admin host itself, NEVER from the
 * shared `APP_BASE_URL` env var (which points at 2nd Act, https://2ndactcapital.com).
 *
 * Callback-base-URL bug (this sprint): tenant SELECTION was already correct
 * (admin.hollisworks.com → Hollisworks Auth0 tenant), but the SDK still built
 * `redirect_uri` from `appBaseUrl ?? process.env.APP_BASE_URL`. Since neither
 * Auth0 client passed `appBaseUrl`, the Hollisworks client inherited 2nd Act's
 * `APP_BASE_URL` and sent `redirect_uri=https://2ndactcapital.com/auth/callback`
 * — the WRONG base domain — which Auth0 rejected with "Callback URL mismatch."
 */
export const HOLLISWORKS_ADMIN_BASE_URL = `https://${HOLLISWORKS_ADMIN_HOST}`;

/**
 * The API audience the Hollisworks staff access token must be minted for.
 *
 * AUDIENCE BUG (this sprint — the THIRD field with the identical shape as the
 * tenant-domain and callback-base-URL bugs): `admin.hollisworks.com` login sent
 * `audience=https://api.2ndactcapital.com` to the SEPARATE Hollisworks Auth0
 * tenant. That tenant has NO resource server (API) registered under 2nd Act's
 * identifier, so Auth0's /authorize endpoint rejected the request outright with
 *   "Service not found: https://api.2ndactcapital.com".
 *
 * Root cause: `resolveAuthTenantForHost` derived the audience as
 *   env.HOLLISWORKS_AUTH0_AUDIENCE || "https://api.2ndactcapital.com"
 * i.e. it SILENTLY fell back to 2nd Act's audience — the exact bug shape as
 * `domain ?? AUTH0_DOMAIN` and `appBaseUrl ?? APP_BASE_URL`. Setting
 * HOLLISWORKS_AUTH0_AUDIENCE in Vercel could not be *verified* to take effect,
 * because a silent default is indistinguishable from a working value: any gap in
 * env propagation reverts to precisely `https://api.2ndactcapital.com`, the very
 * string in the error. The audience is a TENANT-SCOPED identifier — a 2nd Act
 * value here is always wrong for the Hollisworks tenant.
 *
 * The Hollisworks tenant's own API is `https://api.hollisworks.com`. Like
 * `appBaseUrl`, this is Hollisworks-specific by DEFAULT (host-derived, never
 * 2nd Act) and overridable via HOLLISWORKS_AUTH0_AUDIENCE — see
 * `hollisworksAudience` below, which FAILS LOUD instead of ever returning
 * 2nd Act's audience.
 */
export const HOLLISWORKS_API_AUDIENCE = "https://api.hollisworks.com";

/** 2nd Act's API audience — the value that must NEVER leak into the Hollisworks tenant. */
export const TWOACT_API_AUDIENCE = "https://api.2ndactcapital.com";

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
 * Resolve the base URL the Hollisworks Auth0 client must build `redirect_uri`
 * from. Defaults to the admin host itself (`https://admin.hollisworks.com`);
 * overridable via `HOLLISWORKS_APP_BASE_URL` for non-prod hosts (e.g. a preview
 * deployment). The override MUST be an absolute https URL — if it can't be
 * parsed as one, we FAIL LOUD rather than risk silently falling back to a wrong
 * (2nd Act) base, the same discipline as the tenant-config resolver below.
 *
 * Returns the URL's ORIGIN only (no path/query) so `createRouteUrl` appends
 * `/auth/callback` cleanly.
 */
export function hollisworksAppBaseUrl(env = process.env) {
  const raw = env.HOLLISWORKS_APP_BASE_URL;
  const value =
    raw && String(raw).trim() ? String(raw).trim() : HOLLISWORKS_ADMIN_BASE_URL;
  let url;
  try {
    url = new URL(value);
  } catch {
    throw new HollisworksAuthConfigError([
      `a valid HOLLISWORKS_APP_BASE_URL (got "${value}", which is not an absolute URL)`,
    ]);
  }
  if (url.protocol !== "https:") {
    throw new HollisworksAuthConfigError([
      `an https HOLLISWORKS_APP_BASE_URL (got "${value}")`,
    ]);
  }
  return url.origin;
}

/**
 * Resolve the API audience the Hollisworks staff access token is minted for.
 * Defaults to the Hollisworks tenant's OWN API (`https://api.hollisworks.com`);
 * overridable via `HOLLISWORKS_AUTH0_AUDIENCE` for non-prod / alternate APIs.
 *
 * FAIL LOUD, NEVER 2nd Act (same discipline as the tenant-config resolver and
 * `hollisworksAppBaseUrl`):
 *   - a malformed override (not an absolute https URL) throws, rather than
 *     silently degrading to a wrong audience;
 *   - an override equal to 2nd Act's audience (`https://api.2ndactcapital.com`)
 *     throws — that identifier does not exist in the Hollisworks tenant and is
 *     the exact value that produced "Service not found" in production.
 *
 * Returns the EXACT audience string (not `url.origin`/`url.href`) — Auth0
 * audiences are matched verbatim, so an added/stripped trailing slash would
 * itself break resolution.
 */
export function hollisworksAudience(env = process.env) {
  const raw = env.HOLLISWORKS_AUTH0_AUDIENCE;
  const value =
    raw && String(raw).trim() ? String(raw).trim() : HOLLISWORKS_API_AUDIENCE;
  let url;
  try {
    url = new URL(value);
  } catch {
    throw new HollisworksAuthConfigError([
      `a valid HOLLISWORKS_AUTH0_AUDIENCE (got "${value}", which is not an absolute URL)`,
    ]);
  }
  if (url.protocol !== "https:") {
    throw new HollisworksAuthConfigError([
      `an https HOLLISWORKS_AUTH0_AUDIENCE (got "${value}")`,
    ]);
  }
  if (value === TWOACT_API_AUDIENCE) {
    // Refuse to mint a Hollisworks token against 2nd Act's API identifier — the
    // Hollisworks tenant has no such resource server (this is the production bug).
    throw new HollisworksAuthConfigError([
      `a Hollisworks-specific HOLLISWORKS_AUTH0_AUDIENCE — got 2nd Act's own ` +
        `audience "${value}", which does not exist in the Hollisworks tenant ` +
        `(Auth0 returns "Service not found"). Use "${HOLLISWORKS_API_AUDIENCE}".`,
    ]);
  }
  return value;
}

/* ---------------------------------------------------------------------------
 * 2nd Act's OWN client — host-derived appBaseUrl (the FOURTH bug of this shape)
 * ------------------------------------------------------------------------- */

/**
 * The hosts 2nd Act's Auth0 client (`lib/auth0.js`) legitimately serves.
 *
 * THE BUG (live, observed): a signup started on
 * `https://2ndactcapital.hollisworks.com` came back to
 * `https://2ndactcapital.com/auth/callback` — the BARE domain — and Auth0
 * reported "the state parameter is invalid". The transaction cookie (`__txn_*`)
 * was written on the .hollisworks.com host, so the callback on 2ndactcapital.com
 * could not see it and the state lookup failed.
 *
 * Root cause: IDENTICAL in shape to the three Hollisworks bugs above, but on
 * 2nd Act's own client. `lib/auth0.js` passes no `appBaseUrl`, so the SDK used
 * `process.env.APP_BASE_URL` — a STATIC string, `https://2ndactcapital.com`.
 * `resolveAppBaseUrl` returns a string verbatim WITHOUT ever looking at the
 * request, so every host 2nd Act's client serves got the bare domain baked into
 * `redirect_uri`. This was never exercised before tonight's enrollment flow:
 * every prior 2nd Act login happened ON the bare domain, where the static value
 * was accidentally correct.
 *
 * THE FIX is the SAME pattern already proven for the Hollisworks client — pass
 * an ALLOW-LIST ARRAY instead of relying on the static env string, so the SDK
 * derives the base from the REAL request Host and validates it. 2nd Act's client
 * differs from the Hollisworks one in exactly one way: it serves SEVERAL real
 * hosts, so the list has several entries instead of one. Order is irrelevant —
 * `resolveAppBaseUrl` returns the REQUEST's origin once matched, never a list
 * element — so there is no "first entry wins" trap.
 *
 * EXPLICIT LISTING, NEVER WILDCARDS — the same convention as Auth0's own Allowed
 * Callback URLs. Adding a new tenant subdomain is therefore a two-step change:
 * add its origin here (or via `TWOACT_EXTRA_APP_BASE_URLS`) AND add
 * `https://<slug>.hollisworks.com/auth/callback` to 2nd Act's Auth0 tenant.
 */

/** 2nd Act's own root domain — the value APP_BASE_URL holds in production. */
export const TWOACT_PRIMARY_BASE_URL = "https://2ndactcapital.com";

/** The `www` form of that root domain. Confirmed live (resolves in DNS). */
export const TWOACT_WWW_BASE_URL = "https://www.2ndactcapital.com";

/**
 * 2nd Act's Hollisworks-platform tenant subdomain. Not a guess: it is the host
 * in the org's real `organizations.enroll_url`
 * (`https://2ndactcapital.hollisworks.com/enroll`, slug `2ndactcapital`), i.e.
 * the host every invited member actually lands on. THIS is the host the bug hit.
 */
export const TWOACT_TENANT_BASE_URL = "https://2ndactcapital.hollisworks.com";

/**
 * Hosts deliberately EXCLUDED, and why:
 *   - admin.hollisworks.com — a different Auth0 tenant entirely; it never
 *     reaches this client (`getAuthClientForHost`).
 *   - hollisworks.com / www.hollisworks.com — the platform MARKETING apex. It
 *     routes to 2nd Act's client by default but never initiates a login: the
 *     marketing page links to `/firm-search?intent=login`, which sends the
 *     visitor to their own firm's subdomain first. Listing it would mint a 2nd
 *     Act session cookie on the platform apex — a genuine cross-tenant leak. An
 *     `/auth/login` hit there now FAILS LOUD instead of silently redirecting the
 *     visitor into 2nd Act's domain, which is what it does today by accident.
 */

/** Raised when 2nd Act's own appBaseUrl allow-list cannot be built safely. */
export class TwoActAuthConfigError extends Error {
  constructor(detail) {
    super(
      `2nd Act Auth0 appBaseUrl is misconfigured — ${detail}. Refusing to build a ` +
        `client whose redirect_uri could point at the wrong host. Fix APP_BASE_URL / ` +
        `TWOACT_EXTRA_APP_BASE_URLS (Vercel Production + Preview).`
    );
    this.name = "TwoActAuthConfigError";
    this.detail = detail;
  }
}

/** Split a comma-separated env value the same way the Auth0 SDK does. */
function splitBaseUrlList(raw) {
  if (!raw || !String(raw).trim()) return [];
  return String(raw)
    .split(",")
    .map((u) => u.trim())
    .filter(Boolean);
}

/** True for the loopback hosts where plain http is legitimate (local dev only). */
function isLoopbackOrigin(url) {
  return url.hostname === "localhost" || url.hostname === "127.0.0.1";
}

/**
 * Build the appBaseUrl ALLOW-LIST for 2nd Act's Auth0 client.
 *
 * Contents, in order (order does not affect resolution — see above):
 *   1. `APP_BASE_URL` (comma lists supported, exactly as the SDK parses it) —
 *      keeps whatever is deployed today working byte-for-byte, including the
 *      `http://localhost:3000` used by local dev.
 *   2. The three real 2nd Act origins (root, www, tenant subdomain).
 *   3. `TWOACT_EXTRA_APP_BASE_URLS` — comma list; the escape hatch for Vercel
 *      preview deployments and future tenant subdomains, so adding one never
 *      requires a code change.
 *   4. Loopback origins, ONLY when NODE_ENV !== "production", so a dev without
 *      APP_BASE_URL set still gets a working login. Vercel builds (including
 *      previews) run with NODE_ENV=production, so these can never reach a
 *      deployed environment.
 *
 * FAIL LOUD:
 *   - a malformed entry throws rather than being silently dropped;
 *   - a NON-loopback http entry throws. The SDK marks session/transaction
 *     cookies secure only when EVERY entry is https (`client.js:95-104`), so one
 *     stray http production origin would silently downgrade cookie security.
 *
 * Returns deduped ORIGINS (no path/query) so `createRouteUrl` appends
 * `/auth/callback` cleanly — `https://2ndactcapital.com` + `/auth/callback`,
 * byte-identical to what the static string produced.
 */
export function twoActAppBaseUrls(env = process.env) {
  const candidates = [
    ...splitBaseUrlList(env.APP_BASE_URL),
    TWOACT_PRIMARY_BASE_URL,
    TWOACT_WWW_BASE_URL,
    TWOACT_TENANT_BASE_URL,
    ...splitBaseUrlList(env.TWOACT_EXTRA_APP_BASE_URLS),
  ];
  if (env.NODE_ENV !== "production") {
    candidates.push("http://localhost:3000", "http://127.0.0.1:3000");
  }

  const origins = [];
  for (const value of candidates) {
    let url;
    try {
      url = new URL(value);
    } catch {
      throw new TwoActAuthConfigError(
        `"${value}" is not an absolute URL (every appBaseUrl entry must be)`
      );
    }
    if (url.protocol !== "https:" && !(url.protocol === "http:" && isLoopbackOrigin(url))) {
      throw new TwoActAuthConfigError(
        `"${value}" is not https (only http://localhost / http://127.0.0.1 may be ` +
          `plain http; a non-loopback http entry silently disables secure cookies)`
      );
    }
    if (!origins.includes(url.origin)) origins.push(url.origin);
  }
  if (!origins.length) {
    // Unreachable given the constants above, but never hand the SDK an empty list.
    throw new TwoActAuthConfigError("the allow-list resolved to zero origins");
  }
  return origins;
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
    // Cookie-encryption secret. This is the ONE field that MAY legitimately reuse
    // the 2nd Act value: it is a symmetric key used only to encrypt/sign this
    // client's OWN session cookie (`__hw_session`, a distinct name on a distinct
    // host) — it is NOT a tenant-scoped identifier like domain/clientId/audience,
    // so sharing it never authenticates a Hollisworks request against 2nd Act.
    // The share is therefore ALLOWED and documented, but we still FAIL LOUD when
    // NEITHER a Hollisworks-specific nor a shared secret exists (never pass
    // undefined to the SDK).
    const secret = env.HOLLISWORKS_AUTH0_SECRET || env.AUTH0_SECRET;
    const missing = [];
    if (!domain) missing.push("HOLLISWORKS_AUTH0_DOMAIN");
    if (!clientId) missing.push("HOLLISWORKS_AUTH0_CLIENT_ID");
    if (!clientSecret) missing.push("HOLLISWORKS_AUTH0_CLIENT_SECRET");
    if (!secret) missing.push("HOLLISWORKS_AUTH0_SECRET (or shared AUTH0_SECRET)");
    if (missing.length) {
      // FAIL LOUD — do NOT return 2nd Act's config here.
      throw new HollisworksAuthConfigError(missing);
    }
    return {
      tenant: "hollisworks",
      domain,
      clientId,
      clientSecret,
      secret,
      // Audience fix (this sprint): Hollisworks-specific by default
      // (https://api.hollisworks.com), overridable via HOLLISWORKS_AUTH0_AUDIENCE,
      // and FAIL LOUD before it can EVER be 2nd Act's audience. See
      // `hollisworksAudience` for the full root-cause writeup.
      audience: hollisworksAudience(env),
      // Callback-base-URL fix: the client passes this as a single-entry
      // ALLOW-LIST to the SDK so `redirect_uri` is built from the REAL request
      // Host (admin.hollisworks.com), NOT from the shared APP_BASE_URL (2nd Act).
      appBaseUrl: hollisworksAppBaseUrl(env),
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
    // 2nd Act's client now passes this ALLOW-LIST to the SDK (see
    // `twoActAppBaseUrls`) instead of inheriting the static APP_BASE_URL string,
    // so `redirect_uri` follows the REAL request Host. A request from
    // 2ndactcapital.com still resolves to https://2ndactcapital.com — unchanged —
    // while 2ndactcapital.hollisworks.com now correctly stays on its own host.
    appBaseUrl: twoActAppBaseUrls(env),
  };
}
