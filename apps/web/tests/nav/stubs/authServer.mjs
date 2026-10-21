// Test stub: the session GroupLanding sees. Tests set globalThis.__NAV_SESSION.
export async function getHostSession() {
  return globalThis.__NAV_SESSION ?? null;
}
