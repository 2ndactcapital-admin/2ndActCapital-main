import { Auth0Client } from "@auth0/nextjs-auth0/server";
import {
  HOLLISWORKS_ADMIN_HOST,
  resolveAuthTenantForHost,
} from "@/lib/authHostConfig";

/**
 * SECOND, SEPARATE Auth0 tenant — the Hollisworks platform-staff tenant.
 *
 * Used ONLY for `admin.hollisworks.com` (selection lives in `lib/authForHost.js`).
 * This is strictly ADDITIVE: the existing 2nd Act client in `lib/auth0.js` is
 * left byte-for-byte untouched and remains the default for every other domain.
 *
 * Distinctly-named env vars (no collision with the 2nd Act `AUTH0_*` set — see
 * Task 1b):
 *   HOLLISWORKS_AUTH0_DOMAIN         — the Hollisworks tenant domain
 *   HOLLISWORKS_AUTH0_CLIENT_ID      — the admin.hollisworks.com application
 *   HOLLISWORKS_AUTH0_CLIENT_SECRET  — its secret
 *   HOLLISWORKS_AUTH0_SECRET         — cookie-encryption secret (32-byte hex);
 *                                      falls back to AUTH0_SECRET so a single
 *                                      secret works in dev.
 *   HOLLISWORKS_AUTH0_AUDIENCE       — API audience the staff access token is
 *                                      minted for. OPTIONAL override; defaults to
 *                                      the Hollisworks tenant's OWN API
 *                                      (https://api.hollisworks.com). NEVER falls
 *                                      back to 2nd Act's audience — the Hollisworks
 *                                      tenant has no such resource server, so that
 *                                      value makes Auth0 return "Service not found"
 *                                      (the production bug this sprint fixed).
 *
 * Lazily constructed: the client is only built on the first admin.hollisworks.com
 * request. Non-admin traffic never touches this config, so a missing Hollisworks
 * env var can never affect the 2nd Act path.
 */

let _client = null;

export function getHollisworksAuth0() {
  if (_client) return _client;

  // Bug 2 fix: resolve the Hollisworks tenant's config through the single,
  // fail-loud resolver. If HOLLISWORKS_AUTH0_DOMAIN / _CLIENT_ID / _CLIENT_SECRET
  // are missing this THROWS instead of returning 2nd Act's config — so we can
  // never build a client that the Auth0 SDK would silently point at 2nd Act's
  // tenant (via its `domain: options.domain ?? process.env.AUTH0_DOMAIN`
  // fallback). Passing the resolved values EXPLICITLY guarantees the SDK uses
  // the Hollisworks tenant and never the AUTH0_* fallback.
  const cfg = resolveAuthTenantForHost(HOLLISWORKS_ADMIN_HOST);

  _client = new Auth0Client({
    domain: cfg.domain,
    clientId: cfg.clientId,
    clientSecret: cfg.clientSecret,
    secret: cfg.secret,
    // Callback-base-URL fix: pass appBaseUrl as a single-entry ALLOW-LIST
    // (array), NOT a bare string. The SDK builds redirect_uri as
    // `resolveAppBaseUrl(this.appBaseUrl, req)` + "/auth/callback"; given an
    // array it derives the effective base from the REAL request Host header
    // (admin.hollisworks.com) and VALIDATES it against this list — producing
    // https://admin.hollisworks.com/auth/callback. Without this, the SDK fell
    // through to `this.appBaseUrl ?? process.env.APP_BASE_URL`; since we passed
    // nothing, it inherited the shared APP_BASE_URL (https://2ndactcapital.com)
    // and built redirect_uri against 2nd Act's domain — the exact "Callback URL
    // mismatch" Auth0 rejected. The array form ALSO fails loud: a request whose
    // Host is not in the allow-list throws instead of silently using 2nd Act's
    // base. (auth0.js — the 2nd Act client — is left untouched.)
    appBaseUrl: [cfg.appBaseUrl],
    authorizationParameters: {
      audience: cfg.audience,
      scope: "openid profile email",
    },
    // Distinct session cookie so a Hollisworks staff session is never confused
    // with a 2nd Act member session (belt-and-suspenders on top of the fact
    // that admin.hollisworks.com is already a distinct host).
    session: { cookie: { name: "__hw_session" } },
  });
  return _client;
}
