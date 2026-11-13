/**
 * NAV1 — the assistant panel's open / closed state: pure, dependency-free (node-testable), the ONE rule.
 *
 *   * Closed by default for EVERYONE. (Before NAV1 a role → posture table opened it for members; and because it
 *     read the posture off the Auth0 session user, which carries neither the posture nor the role, every user fell
 *     to the "member" default — open.)
 *   * One button in the top bar toggles it (components/assistant/AssistantToggle.jsx).
 *   * The choice is remembered in the EXISTING user-preference store — users.assistant_panel_posture, written by
 *     PATCH /users/me and published on GET /users/me — the same pattern the sidebar pin uses (users.nav_pinned):
 *     per-browser storage first so a reload does not flash, the account value wins once it loads, every toggle
 *     writes both. Storage access is wrapped in try/catch (private mode, disabled storage).
 *   * Only "expanded" opens it. A legacy or unknown stored posture ("collapsible", "", null) is the default: closed.
 */
export const STORAGE_KEY = "assistant-panel-open";
export const OPEN = "expanded";
export const CLOSED = "collapsed";

export const openToPosture = (open) => (open ? OPEN : CLOSED);

/** true / false from per-browser storage, or null when absent, malformed or unreadable. */
export function readStored(storage) {
  try {
    const v = storage ? storage.getItem(STORAGE_KEY) : null;
    return v === "true" ? true : v === "false" ? false : null;
  } catch {
    return null;
  }
}

export function writeStored(storage, open) {
  try {
    if (storage) storage.setItem(STORAGE_KEY, String(!!open));
  } catch {
    // storage unavailable — the account value still carries the choice
  }
}

/** The open state: an explicit account choice wins, then this browser's, else CLOSED. */
export function resolveOpen({ stored = null, accountPosture = null } = {}) {
  if (accountPosture === OPEN) return true;
  if (accountPosture === CLOSED) return false;
  if (stored === true || stored === false) return stored;
  return false;
}

/**
 * The shared state behind the top-bar button and the panel (two siblings in AppShell). `persist(posture)` writes
 * the account preference (the client hook PATCHes /api/users/me); `storage` is window.localStorage or a stub.
 */
export function createPanelStore({ storage = null, persist = null } = {}) {
  let open = false;
  let started = false;
  let accountApplied = false;
  const subs = new Set();
  const emit = () => subs.forEach((fn) => fn());
  return {
    get: () => open,
    subscribe(fn) {
      subs.add(fn);
      return () => subs.delete(fn);
    },
    /** Seed from this browser once (on mount — never during server render). */
    start() {
      if (started) return;
      started = true;
      open = resolveOpen({ stored: readStored(storage) });
      emit();
    },
    /** Apply the account's stored posture once, when /users/me loads. */
    applyAccount(posture) {
      if (accountApplied || posture === undefined) return;
      accountApplied = true;
      if (posture !== OPEN && posture !== CLOSED) return; // no explicit account choice: keep the browser's / default
      open = posture === OPEN;
      writeStored(storage, open);
      emit();
    },
    toggle() {
      open = !open;
      accountApplied = true; // the user's own choice now; a late /users/me must not undo it
      writeStored(storage, open);
      if (persist) {
        try { persist(openToPosture(open)); } catch { /* best effort, like the sidebar pin */ }
      }
      emit();
      return open;
    },
  };
}
