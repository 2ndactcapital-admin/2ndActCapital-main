import { headers } from "next/headers";
import { getAuthClientForHost } from "@/lib/authForHost";

/**
 * Request-scoped, HOST-AWARE Auth0 access for Server Components and Route
 * Handlers.
 *
 * THE BUG THIS FIXES (real, observed in production as "too many redirects"):
 * every authenticated page imported the FIXED 2nd Act client (`@/lib/auth0`)
 * and checked the session with it, regardless of Host. A member who logged in
 * at admin.hollisworks.com holds a session in the SEPARATE Hollisworks Auth0
 * tenant, stored in a DIFFERENT cookie (`__hw_session`, encrypted with the
 * Hollisworks secret). 2nd Act's client only ever reads its own `__session`
 * cookie, so `auth0.getSession()` returned null for a perfectly valid
 * Hollisworks session. The page then redirected to /auth/login, the (correctly
 * host-aware) middleware saw an existing Hollisworks session, and sent the
 * browser straight back — an infinite redirect loop.
 *
 * The selection rule itself is NOT reinvented here: this delegates to the same
 * `getAuthClientForHost(host)` that `app/login/page.js` and `proxy.js` already
 * use, called the same way (the real `Host` request header). For every non-admin
 * host that function returns the EXISTING 2nd Act client unchanged, so 2nd Act's
 * own behavior is provably identical to before.
 *
 * Deliberately a separate module from `lib/authForHost.js`: that one is imported
 * by `proxy.js` (middleware), which must not pull in `next/headers`.
 */

/** The Auth0 client for the CURRENT request's Host. */
export async function getRequestAuthClient() {
  const host = (await headers()).get("host") || "";
  return getAuthClientForHost(host);
}

/**
 * The session for the current request, read with the client belonging to the
 * request's OWN tenant. Drop-in replacement for `auth0.getSession()`.
 */
export async function getHostSession() {
  const client = await getRequestAuthClient();
  return client.getSession();
}

/**
 * The access token for the current request, minted for the tenant's OWN API
 * audience. Drop-in replacement for `auth0.getAccessToken()`.
 */
export async function getHostAccessToken() {
  const client = await getRequestAuthClient();
  return client.getAccessToken();
}
