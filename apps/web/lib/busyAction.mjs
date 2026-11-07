// CATCHALL1 Task 3 — THE busy state of a slow action, as pure state transitions (no React here, so node tests drive
// the real logic). One action runs at a time; while pending the control is disabled, shows "Working…" and carries
// aria-busy; on error it is restored (enabled, its own label) and the error is shown. components/ui/Busy.jsx is the
// only React wrapper over this — no screen keeps its own copy.

export const WORKING_LABEL = "Working…";
export const IDLE = Object.freeze({ busy: false, error: null });

/** The message shown for a failed action (an Error, a string, or an API body with error / detail). */
export function errorText(err) {
  if (err == null) return "Something went wrong";
  if (typeof err === "string") return err;
  if (typeof err.message === "string" && err.message) return err.message;
  if (typeof err.error === "string") return err.error;
  if (typeof err.detail === "string") return err.detail;
  return "Something went wrong";
}

/**
 * Run ``fn`` under the busy state. ``get()`` reads the current state, ``set(s)`` stores it. A second call while one
 * is pending is ignored (returns undefined) — a double click never sends two requests. Resolves to fn's result, or
 * undefined when it threw (the error is in the state, never swallowed silently).
 */
export async function runBusy(get, set, fn) {
  if (get()?.busy) return undefined;
  set({ busy: true, error: null });
  try {
    const out = await fn();
    set({ busy: false, error: null });
    return out;
  } catch (err) {
    set({ busy: false, error: errorText(err) });
    return undefined;
  }
}

/** The props a busy-aware button renders with (disabled while pending or when the caller disables it). */
export function busyButtonProps({ busy = false, disabled = false, label, busyLabel = WORKING_LABEL } = {}) {
  return {
    disabled: Boolean(busy || disabled),
    "aria-busy": busy ? "true" : "false",
    "data-busy-button": "1",
    text: busy ? busyLabel : label,
    spinner: Boolean(busy),
  };
}

/** Throw the API's own message for a non-OK fetch Response (so runBusy shows it); returns the parsed body. */
export async function jsonOrThrow(res) {
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    const msg = typeof body?.error === "string" ? body.error
      : typeof body?.detail === "string" ? body.detail : `Request failed (${res.status})`;
    throw new Error(msg);
  }
  return body;
}
