import { getAuthClientForHost } from "./lib/authForHost";

export async function proxy(request) {
  // Pick the Auth0 client by request Host. admin.hollisworks.com authenticates
  // against the separate Hollisworks tenant; EVERY other host keeps using the
  // existing 2nd Act client, unchanged. This is what makes the mounted auth
  // routes (/auth/login, /auth/callback, /auth/logout) run against the correct
  // tenant instead of silently falling through to 2nd Act's tenant.
  const authClient = getAuthClientForHost(request.headers.get("host"));

  /* highlight-start auth-response */
  const authResponse = await authClient.middleware(request);
  /* highlight-end auth-response */

  // Always return the auth response.
  //
  // Note: The auth response forwards requests to your app routes by default.
  // If you need to block requests, do it before calling authClient.middleware()
  // or copy the authResponse headers except for x-middleware-next to your
  // blocking response.
  /* highlight-start auth-response */
  return authResponse;
  /* highlight-end auth-response */
}

export const config = {
  matcher: [
    "/((?!_next/static|_next/image|favicon.ico|sitemap.xml|robots.txt).*)",
  ],
};
