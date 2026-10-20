// Test stub: the /users/me payload GroupLanding sees. Tests set globalThis.__NAV_ME
// (an Error instance makes getMe() reject, like a failed request).
export async function getMe() {
  const me = globalThis.__NAV_ME;
  if (me instanceof Error) throw me;
  return me ?? null;
}
