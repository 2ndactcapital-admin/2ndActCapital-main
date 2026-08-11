import { auth0 } from "@/lib/auth0";
import { getHollisworksAuth0 } from "@/lib/auth0Hollisworks";
import {
  HOLLISWORKS_ADMIN_HOST,
  isHollisworksAdminHost,
} from "@/lib/authHostConfig";

/**
 * Host → Auth0 client selection (Hollisworks admin tenant wiring).
 *
 * `admin.hollisworks.com` is the ONLY host that authenticates against the new,
 * separate Hollisworks Auth0 tenant. Every other host — the 2nd Act tenant
 * subdomain, the Hollisworks marketing apex, localhost — continues to use the
 * EXISTING 2nd Act client (`lib/auth0.js`) with no change whatsoever.
 *
 * The host predicate and the tenant-config resolution now live in the pure,
 * dependency-free `lib/authHostConfig.mjs` so there is a SINGLE source of truth
 * that both the app (middleware + login page) and the hermetic Node test harness
 * exercise. `HOLLISWORKS_ADMIN_HOST` / `isHollisworksAdminHost` are re-exported
 * here so existing importers (e.g. app/login/page.js) keep working unchanged.
 */

export { HOLLISWORKS_ADMIN_HOST, isHollisworksAdminHost };

/**
 * Return the Auth0 client for a given request Host.
 *
 * DEFAULT is the existing 2nd Act client — so for every non-admin host the
 * behavior is provably identical to before this sprint. Only the exact
 * admin.hollisworks.com host diverges to the Hollisworks tenant. If the
 * Hollisworks tenant is misconfigured, `getHollisworksAuth0()` throws (fail
 * loud) rather than silently authenticating admin staff against 2nd Act.
 */
export function getAuthClientForHost(host) {
  return isHollisworksAdminHost(host) ? getHollisworksAuth0() : auth0;
}
