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

// ─── "Run on your laptop" dialog (bulkfetchui2) ─────────────────────────────
// The steps come ONLY from GET workers' `laptop_instructions` (server-built
// from the EDGAR_BULK_LAPTOP_WORKER_COMMAND setting); nothing here carries a
// copy of the command.

export const LAPTOP_DIALOG_PREF_KEY = "hollisworks.edgarBulk.hideLaptopDialogWhenOnline";
export const LAPTOP_DIALOG_RECHECK_MS = 5000;

/** Per-browser "don't show again while the worker is running". Any storage failure reads as false. */
export function readHideWhenOnline(storage) {
  try {
    return storage?.getItem(LAPTOP_DIALOG_PREF_KEY) === "1";
  } catch {
    return false;
  }
}

export function writeHideWhenOnline(storage, value) {
  try {
    if (value === true) storage?.setItem(LAPTOP_DIALOG_PREF_KEY, "1");
    else storage?.removeItem(LAPTOP_DIALOG_PREF_KEY);
    return true;
  } catch {
    return false;
  }
}

/**
 * Whether POST /runs's answer opens the dialog. Only a successful Laptop
 * queue does; Render keeps its inline refusal. The don't-show preference
 * suppresses it only when the response says a worker IS online.
 */
export function dialogAfterQueue(res, target, hideWhenOnline) {
  if (target !== "laptop" || res?.ok !== true) return null;
  const p = res.payload ?? {};
  const online = p.worker_online === true;
  if (online && hideWhenOnline === true) return null;
  return { source: "queue", runId: p.run?.id ?? null };
}

/** "Show these steps": the same dialog, nothing queued. */
export function stepsDialog() {
  return { source: "steps", runId: null };
}

/**
 * Queue a run and decide the dialog. `queueRun` is the POST (injected, so the
 * node tests can count calls). Returns the inline outcome and the dialog (or null).
 */
export async function queueAndOpen({ queueRun, params, target, hideWhenOnline }) {
  const res = await queueRun({ params, target });
  return { res, outcome: queueOutcome(res), dialog: dialogAfterQueue(res, target, hideWhenOnline) };
}

/** The dialog's status banner from the latest GET workers. */
export function laptopDialogStatus(workers, { queued = true } = {}) {
  const laptop = workers?.laptop;
  if (laptop?.online === true) {
    const host = laptop.worker?.host ?? "unknown host";
    return {
      online: true,
      tone: "success",
      host,
      text: queued
        ? `Laptop worker running on ${host} — your run will start automatically`
        : `Laptop worker running on ${host} — queued runs start automatically`,
    };
  }
  return {
    online: false,
    tone: "warning",
    host: null,
    text: queued
      ? "No laptop worker running — your run is queued and will start when you start one"
      : "No laptop worker running — queued runs will start when you start one",
  };
}

export function stepHasCommand(step) {
  return typeof step?.command === "string" && step.command.trim().length > 0;
}

/**
 * Split the server's steps for rendering: the "start" steps collapse under
 * "How to start the worker" ONLY while a worker is online; everything else
 * stays visible. Order is the server's.
 */
export function laptopDialogSteps(workers, online) {
  const steps = Array.isArray(workers?.laptop_instructions) ? workers.laptop_instructions : [];
  if (online !== true) return { visible: steps, collapsed: [] };
  return {
    visible: steps.filter((s) => s.section !== "start"),
    collapsed: steps.filter((s) => s.section === "start"),
  };
}

// ─── bulkfetchui3: the Institutions table ──────────────────────────────────

/** While a bulk run is queued or running, the Institutions counts re-read this often. */
export const INSTITUTIONS_REFRESH_MS = 60000;

/** Sort keys: "name", "entities", "total", "downloaded", "stored", or "year:<YYYY>". */
export const DEFAULT_INSTITUTION_SORT = { key: "name", dir: "asc" };

/** Click a header: the same column flips direction; a new column starts ascending. */
export function nextSort(sort, key) {
  if (sort?.key === key) return { key, dir: sort.dir === "asc" ? "desc" : "asc" };
  return { key, dir: "asc" };
}

function rowCount(r, years) {
  return r.total ?? years.reduce((s, y) => s + (r.years?.[y] ?? 0), 0);
}

/** The value a row (institution or filer) sorts by: a lowercased string for
 * names, a number for every count. A filer has no Entities count (0). */
export function sortValue(row, key, years = []) {
  if (key === "name") return String(row.name ?? "").toLowerCase();
  if (key === "entities") return Array.isArray(row.filers) ? row.filers.length : 0;
  if (key === "total") return Number(rowCount(row, years)) || 0;
  if (key === "downloaded" || key === "stored") return Number(row[key] ?? 0) || 0;
  if (key.startsWith("year:")) return Number(row.years?.[key.slice(5)] ?? 0) || 0;
  return 0;
}

/** Comparator for one sort: numeric for counts, case-insensitive for names;
 * ties fall back to the name (ascending) so the order is stable. */
export function compareBy(sort, years = []) {
  const sign = sort?.dir === "desc" ? -1 : 1;
  const key = sort?.key ?? "name";
  return (a, b) => {
    const va = sortValue(a, key, years);
    const vb = sortValue(b, key, years);
    let c = typeof va === "string" ? va.localeCompare(vb) : va - vb;
    if (c === 0 && key !== "name") c = sortValue(a, "name").localeCompare(sortValue(b, "name"));
    return sign * c;
  };
}

/** Institutions sorted, each with its filers sorted the same way among themselves. */
export function sortInstitutions(rows, sort, years = []) {
  const cmp = compareBy(sort, years);
  return [...(rows ?? [])].sort(cmp).map((r) => ({ ...r, filers: [...(r.filers ?? [])].sort(cmp) }));
}

/** "Index refreshing" / "Index refresh queued" / null, from the runs payload. */
export function indexRefreshState(runsPayload) {
  const rows = (runsPayload?.rows ?? []).filter((r) => r.run_kind === "refresh_index");
  if (rows.some((r) => r.status === "running" && r.display_status !== "worker_lost")) return "running";
  if (rows.some((r) => r.status === "queued")) return "queued";
  return null;
}

/** True while a BULK download (not an index refresh) is queued or running. */
export function bulkRunsActive(runsPayload) {
  return (runsPayload?.rows ?? []).some(
    (r) => r.run_kind === "bulk_fetch" && (r.status === "queued" || r.status === "running"),
  );
}

/** "h:mm" for the Refresh button's "as of" note. */
export function fmtAsOf(value) {
  if (!value) return "";
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}
