import { auth0 } from "@/lib/auth0";
import { getHollisworksAuth0 } from "@/lib/auth0Hollisworks";

/**
 * Host → Auth0 client selection (Hollisworks admin tenant wiring).
 *
 * `admin.hollisworks.com` is the ONLY host that authenticates against the new,
 * separate Hollisworks Auth0 tenant. Every other host — the 2nd Act tenant
 * subdomain, the Hollisworks marketing apex, localhost — continues to use the
 * EXISTING 2nd Act client (`lib/auth0.js`) with no change whatsoever.
 *
 * Kept in its own module (not inlined in proxy.js) so the login page and any
 * future admin route resolve the exact same client the middleware used, and so
 * the selection rule has a single source of truth.
 */

export const HOLLISWORKS_ADMIN_HOST = "admin.hollisworks.com";

/** True when the request's Host is the Hollisworks admin surface. */
export function isHollisworksAdminHost(host) {
  if (!host) return false;
  // Strip any :port and lowercase for a robust compare.
  const bare = String(host).split(":")[0].trim().toLowerCase();
  return bare === HOLLISWORKS_ADMIN_HOST;
}

/**
 * Return the Auth0 client for a given request Host.
 *
 * DEFAULT is the existing 2nd Act client — so for every non-admin host the
 * behavior is provably identical to before this sprint. Only the exact
 * admin.hollisworks.com host diverges to the Hollisworks tenant.
 */
export function getAuthClientForHost(host) {
  return isHollisworksAdminHost(host) ? getHollisworksAuth0() : auth0;
}
