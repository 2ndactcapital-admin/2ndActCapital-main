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
 *                                      minted for; defaults to the SAME platform
 *                                      API so the one FastAPI backend accepts it,
 *                                      differentiated only by issuer.
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
