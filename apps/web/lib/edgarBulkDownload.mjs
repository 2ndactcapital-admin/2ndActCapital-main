// bulkfetchui — the "Bulk download" tab's pure logic (no React, no fetch), so
// node:test can feed it the API's real envelopes (tests/edgar/).
//
// Write gating: every write control (Queue run, Refresh index, Stop / Cancel /
// Requeue) renders only when `permissions.can_write === true` — no truthy
// fallback. A lost envelope fails CLOSED.

export const PRESETS = [
  { key: "2025-2026", label: "2025–2026" },
  { key: "last12", label: "Last 12 months" },
  { key: "ytd", label: "Year to date" },
  { key: "custom", label: "Custom" },
];

function iso(d) {
  return d.toISOString().slice(0, 10);
}

/** {from, to} for a preset, ending at the last indexed date (never past it). */
export function presetRange(key, lastIndexed) {
  if (!lastIndexed) return null;
  const last = new Date(`${lastIndexed}T00:00:00Z`);
  if (Number.isNaN(last.getTime())) return null;
  const to = iso(last);
  if (key === "2025-2026") return { from: "2025-01-01", to };
  if (key === "ytd") return { from: `${last.getUTCFullYear()}-01-01`, to };
  if (key === "last12") {
    const from = new Date(last);
    from.setUTCFullYear(from.getUTCFullYear() - 1);
    from.setUTCDate(from.getUTCDate() + 1);
    return { from: iso(from), to };
  }
  return null;
}

export function canWrite(payload) {
  return payload?.permissions?.can_write === true;
}

/** The params body POST /admin/edgar/bulk/preview and /runs take. */
export function buildParams({ banks, from, to, kinds, peek, concurrency, maxRps }) {
  return {
    banks: [...banks],
    from: from || null,
    to: to || null,
    kinds: [...kinds],
    peek: peek === true,
    keep_preliminaries: false,
    concurrency: Number(concurrency),
    max_rps: Math.min(Number(maxRps), 8),
  };
}

export function previewReady(params) {
  return params.banks.length > 0 && Boolean(params.from) && params.kinds.length > 0;
}

/** Laptop worker status line from GET /admin/edgar/bulk/workers. */
export function laptopStatus(workers) {
  const laptop = workers?.laptop;
  if (!laptop) return { online: false, text: "worker status unknown", command: null };
  if (laptop.online === true) {
    return { online: true, text: `worker running on ${laptop.worker?.host ?? "unknown host"}`, command: null };
  }
  return {
    online: false,
    text: "no worker — start it with",
    command: laptop.tmux_command ?? laptop.start_command ?? null,
  };
}

/** Render status. "not set up" stays SELECTABLE so the refusal can be shown. */
export function renderStatus(workers) {
  const render = workers?.render;
  if (!render) return { state: "unknown", text: "status unknown", greyed: true };
  if (render.configured === true && render.online === true) {
    return { state: "online", text: `online${render.worker?.host ? ` (${render.worker.host})` : ""}`, greyed: false };
  }
  if (render.configured === true) return { state: "offline", text: "configured, no recent heartbeat", greyed: true };
  return { state: "not_set_up", text: "not set up", greyed: true };
}

/** Row actions the caller may take: the server's per-row `actions`, AND write permission. */
export function rowActions(run, payload) {
  if (!canWrite(payload)) return [];
  const a = run?.actions ?? {};
  return ["stop", "cancel", "requeue"].filter((k) => a[k] === true);
}

export function progressPercent(run) {
  const total = Number(run?.total ?? 0);
  const done = Number(run?.done ?? 0);
  if (!total) return run?.status === "succeeded" ? 100 : 0;
  return Math.max(0, Math.min(100, Math.round((done / total) * 100)));
}

export function fmtDuration(seconds) {
  if (seconds === null || seconds === undefined || Number.isNaN(Number(seconds))) return "";
  const s = Math.max(0, Math.round(Number(seconds)));
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (d) return `${d}d ${h}h`;
  if (h) return `${h}h ${String(m).padStart(2, "0")}m`;
  return `${m}m`;
}

/** Auto-refresh the runs table while anything is queued or running. */
export function runsActive(runsPayload) {
  return (runsPayload?.rows ?? []).some((r) => r.status === "queued" || r.status === "running");
}

/** The inline message after POST /runs: queued (with the start command when no worker is online) or refused. */
export function queueOutcome(res) {
  if (!res?.ok) {
    return {
      error: true,
      text: res?.error ?? "Could not queue the run.",
      setupAnchor: res?.detail?.setup_anchor ?? null,
      lastIndexed: res?.detail?.last_indexed_date ?? null,
    };
  }
  const p = res.payload ?? {};
  if (p.worker_online === false) {
    return {
      error: false,
      text: "Queued. No laptop worker is running — start one with:",
      command: p.tmux_command ?? p.start_command ?? null,
    };
  }
  return { error: false, text: "Queued. The worker will pick it up shortly.", command: null };
}
