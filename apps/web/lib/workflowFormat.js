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
