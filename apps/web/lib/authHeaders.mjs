// B4 — the bearer header for server-side API calls, as a pure function so it
// can be tested in plain Node without Next's request context.
//
// getAccessToken() is awaited inside a try whose ONLY job is to hand a failure
// to `onExpired`. onExpired runs from the catch, so whatever it throws (in
// lib/api.js: next/navigation's redirect(), i.e. NEXT_REDIRECT) propagates to
// the caller — nothing here can swallow it. A token that is simply absent (no
// throw) still yields {} exactly as before.

// The Auth0 v4 SDK's login route, mounted by proxy.js (the app's pages
// redirect to "/auth/login?returnTo=..." the same way).
export const LOGIN_PATH = "/auth/login";

export async function bearerHeaders(authClient, onExpired) {
  let result;
  try {
    result = await authClient.getAccessToken();
  } catch (err) {
    return onExpired(err);
  }
  const token = result?.token || result?.accessToken;
  if (token) return { Authorization: `Bearer ${token}` };
  return {};
}
