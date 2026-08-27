// Shared presentation helpers for the Workflow Manager Phase 4 read consoles.
// Pure functions only — token-based Tailwind classes, never literal palette hex.

export function formatDateTime(value) {
  if (!value) return "—";
  try {
    return new Date(value).toLocaleString(undefined, {
      year: "numeric",
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return String(value);
  }
}

// Understated status pill classes (brand: quiet, no bright greens). Attention
// states (held/failed) get the gold accent; terminal/neutral states stay muted.
export function statusPillClass(status) {
  const base =
    "inline-flex items-center rounded border px-2 py-0.5 text-xs font-medium";
  switch (status) {
    case "held":
    case "failed":
      return `${base} border-gold text-gold`;
    case "running":
    case "active":
      return `${base} border-navy text-navy`;
    case "completed":
    case "approved":
      return `${base} border-border text-text-secondary`;
    default:
      // pending / proposed / skipped / cancelled / anything else
      return `${base} border-border text-text-muted`;
  }
}

export function personLabel(name, email) {
  return name || email || "—";
}

/**
 * A duration, in the coarsest unit that still says something true.
 *
 * `null` means the API did not measure one — a run still in progress, or a step
 * whose two timestamps are written by a single statement. It renders as an
 * em-dash, NOT as "0s": see NOT_MEASURED below for why that distinction is the
 * whole point.
 */
export function formatDuration(seconds) {
  if (seconds === null || seconds === undefined) return "—";
  const value = Number(seconds);
  if (!Number.isFinite(value) || value < 0) return "—";
  if (value < 1) return `${Math.round(value * 1000)} ms`;
  if (value < 60) return `${value < 10 ? value.toFixed(1) : Math.round(value)} s`;
  const minutes = Math.floor(value / 60);
  if (minutes < 60) return `${minutes}m ${Math.round(value % 60)}s`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ${minutes % 60}m`;
  return `${Math.floor(hours / 24)}d ${hours % 24}h`;
}

/**
 * What something whose duration is not a measurement should say instead.
 *
 * This applies at BOTH levels, and the run level is the one that is easy to get
 * wrong. Postgres `now()` is the TRANSACTION timestamp:
 *
 *   · A Service Task's `started_at` and `completed_at` are written by ONE
 *     post-hoc UPDATE, so their interval is always exactly zero.
 *   · A RUN that finishes inside its own start call is inserted on one
 *     connection and completed on another whose transaction opened first, so
 *     its interval comes out NEGATIVE — measured at -0.36s on a real run.
 *
 * Neither zero is a fast step and neither negative is a time-travelling run;
 * both are artifacts of how the row was recorded. The API sends
 * `duration_measured: false` in both cases and the screen prints this rather
 * than a number, because a "0s" in a duration column is indistinguishable from
 * a real measurement and would be read as one.
 */
export const NOT_MEASURED = "not measured";

/** The same fact, spelled out for a tooltip where there is room for it. */
export const NOT_MEASURED_WHY =
  "The two timestamps were recorded in overlapping transactions, so their " +
  "difference is not an elapsed time.";
