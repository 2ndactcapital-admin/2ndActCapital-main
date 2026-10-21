"use client";

/**
 * EdgarBulkDownload — the "Bulk download" tab of the EDGAR Pipeline screen
 * (bulkfetchui). Super Admin only (FastAPI enforces it).
 *
 * Pick institutions (each expandable to its legal entities and CIKs), a time
 * frame and document kinds; a debounced PREVIEW shows counts by institution ×
 * year, the entities included, ETA, storage and cost. "Queue run" QUEUES the
 * run — the API never downloads: a worker process on the laptop (default) or,
 * once set up, a Render background worker claims it. The runs table below
 * shows progress and offers Stop / Cancel / Requeue, auto-refreshing every
 * 10 s while a run is queued or running.
 *
 * Queuing on the Laptop opens the "Run on your laptop" dialog
 * (LaptopWorkerDialog): live worker status re-checked every 5 s, and the
 * numbered steps from GET workers' `laptop_instructions` (server-built — the
 * component holds no copy of the start command). "Show these steps" beside the
 * Laptop radio opens it without queuing.
 *
 * Every label (document kinds, targets, statuses, the Render refusal) comes
 * from the response envelope (Rule 1). Write controls render only inside a
 * `permissions.can_write === true` check (lib/edgarBulkDownload.mjs canWrite)
 * — a lost envelope renders a read-only screen.
 */

import { useCallback, useEffect, useMemo, useRef, useState, useTransition } from "react";

import LaptopWorkerDialog, { CopyButton } from "@/components/admin/LaptopWorkerDialog";
import DataGrid from "@/components/ui/DataGrid";
import {
  LAPTOP_DIALOG_RECHECK_MS,
  PRESETS,
  buildParams,
  canWrite,
  fmtDuration,
  laptopStatus,
  presetRange,
  previewReady,
  progressPercent,
  queueAndOpen,
  queueOutcome,
  readHideWhenOnline,
  renderStatus,
  rowActions,
  runsActive,
  stepsDialog,
  writeHideWhenOnline,
} from "@/lib/edgarBulkDownload.mjs";
import {
  bulkRunAction,
  loadBulkInstitutionsAction,
  loadBulkRunsAction,
  loadBulkWorkersAction,
  previewBulkAction,
  queueBulkRunAction,
  queueRefreshIndexAction,
} from "@/lib/edgarPipelineActions";

const CARD = "rounded-md border border-[var(--2a-border)] bg-[var(--2a-bg-card)]";
const CONTROL =
  "rounded border border-[var(--2a-border)] bg-[var(--2a-bg-card)] px-2 py-1.5 text-xs text-[var(--2a-text)] focus:outline-none focus:ring-1 focus:ring-[var(--2a-gold)]";
const EYEBROW =
  "block text-[10px] font-semibold uppercase tracking-[0.12em] text-[var(--2a-text-muted)]";
const BUTTON =
  "rounded bg-[var(--2a-navy)] px-3 py-1.5 text-xs font-medium text-white disabled:opacity-50";
const GHOST =
  "rounded border border-[var(--2a-border)] px-3 py-1.5 text-xs text-[var(--2a-navy)] hover:bg-[var(--2a-bg)] disabled:opacity-50";
const ERROR_INK = "#9B2335";
const SUCCESS_INK = "#2D6A4F";
const HIGHLIGHT_BG = "rgba(232, 213, 163, 0.35)";   // Gold Light, tinted
const PREVIEW_DEBOUNCE_MS = 450;
const RUNS_REFRESH_MS = 10000;
const RUNS_PAGE_SIZE = 25;

function fmtWhen(v) {
  if (!v) return "";
  const d = new Date(v);
  return Number.isNaN(d.getTime()) ? String(v) : d.toLocaleString();
}

function fmtInt(n) {
  return n === null || n === undefined ? "" : Number(n).toLocaleString();
}

function Dot({ on }) {
  return (
    <span
      aria-hidden="true"
      className="inline-block h-2 w-2 rounded-full"
      style={{ background: on ? SUCCESS_INK : "#CBD5E1" }}
    />
  );
}

function browserStorage() {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

function Command({ text }) {
  if (!text) return null;
  return (
    <span className="inline-flex flex-wrap items-center gap-2">
      <code className="rounded bg-[var(--2a-bg)] px-1.5 py-0.5 text-[11px] text-[var(--2a-text)]">{text}</code>
      <CopyButton text={text} />
    </span>
  );
}

// ─── Institutions ───────────────────────────────────────────────────────────

function InstitutionPicker({ rows, selected, onToggle, onAll, onNone }) {
  const [search, setSearch] = useState("");
  const [open, setOpen] = useState(() => new Set());
  const needle = search.trim().toLowerCase();
  const shown = rows.filter(
    (r) =>
      !needle ||
      r.name.toLowerCase().includes(needle) ||
      r.filers.some((f) => f.name.toLowerCase().includes(needle) || f.cik.includes(needle)),
  );
  const years = useMemo(() => [...new Set(rows.flatMap((r) => Object.keys(r.years)))].sort().slice(-3), [rows]);

  function toggleOpen(name) {
    setOpen((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  }

  return (
    <div className={`${CARD} p-4`}>
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <h3 className="mr-auto text-sm font-semibold text-[var(--2a-navy)]">Institutions</h3>
        <input
          className={`${CONTROL} w-48`}
          placeholder="Search institutions, entities, CIKs"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <button type="button" className={GHOST} onClick={() => onAll(shown.map((r) => r.name))}>
          Select all
        </button>
        <button type="button" className={GHOST} onClick={onNone}>
          Select none
        </button>
      </div>
      <p className="mb-2 text-[11px] text-[var(--2a-text-muted)]">
        Selected filings still to fetch, by year ({years.join(", ")} shown).
      </p>
      <ul className="max-h-80 overflow-y-auto">
        {shown.map((r) => (
          <li key={r.name} className="border-t border-[var(--2a-border)] py-1.5">
            <div className="flex items-center gap-2 text-xs">
              <input
                type="checkbox"
                aria-label={`Select ${r.name}`}
                checked={selected.has(r.name)}
                onChange={() => onToggle(r.name)}
              />
              <button
                type="button"
                className="text-left text-[var(--2a-text)] hover:text-[var(--2a-navy)]"
                onClick={() => toggleOpen(r.name)}
                aria-expanded={open.has(r.name)}
              >
                <span className="mr-1 inline-block w-3 text-[var(--2a-text-muted)]">{open.has(r.name) ? "▾" : "▸"}</span>
                {r.name}
                <span className="ml-1 text-[var(--2a-text-muted)]">
                  ({r.filers.length} {r.filers.length === 1 ? "entity" : "entities"})
                </span>
              </button>
              <span className="ml-auto flex gap-3 tabular-nums text-[var(--2a-text-secondary)]">
                {years.map((y) => (
                  <span key={y} title={y}>
                    {fmtInt(r.years[y] ?? 0)}
                  </span>
                ))}
              </span>
            </div>
            {open.has(r.name) && (
              <table className="ml-8 mt-1 w-[calc(100%-2rem)] text-[11px]">
                <tbody>
                  {r.filers.map((f) => (
                    <tr key={f.cik}>
                      <td className="py-0.5 text-[var(--2a-text-secondary)]">{f.name}</td>
                      <td className="py-0.5 tabular-nums text-[var(--2a-text-muted)]">CIK {f.cik}</td>
                      <td className="py-0.5 text-[var(--2a-text-muted)]">{f.role ?? ""}</td>
                      {years.map((y) => (
                        <td key={y} className="py-0.5 text-right tabular-nums text-[var(--2a-text)]">
                          {fmtInt(f.years[y] ?? 0)}
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </li>
        ))}
        {!shown.length && <li className="py-3 text-xs text-[var(--2a-text-muted)]">No institution matches.</li>}
      </ul>
    </div>
  );
}

// ─── Preview ────────────────────────────────────────────────────────────────

function PreviewPanel({ preview, loading, error }) {
  if (error) {
    return (
      <div className={`${CARD} p-4`}>
        <h3 className="mb-1 text-sm font-semibold text-[var(--2a-navy)]">Preview</h3>
        <p className="text-xs" style={{ color: ERROR_INK }}>
          {error}
        </p>
      </div>
    );
  }
  if (!preview) {
    return (
      <div className={`${CARD} p-4`}>
        <h3 className="mb-1 text-sm font-semibold text-[var(--2a-navy)]">Preview</h3>
        <p className="text-xs text-[var(--2a-text-muted)]">
          {loading ? "Counting…" : "Pick at least one institution, a From date and a document kind."}
        </p>
      </div>
    );
  }
  const est = preview.estimate ?? {};
  return (
    <div className={`${CARD} p-4`}>
      <div className="mb-3 flex items-baseline gap-3">
        <h3 className="text-sm font-semibold text-[var(--2a-navy)]">Preview</h3>
        {loading && <span className="text-[11px] text-[var(--2a-text-muted)]">updating…</span>}
        <span className="ml-auto text-[11px] text-[var(--2a-text-muted)]">counted in {preview.elapsed_ms} ms</span>
      </div>
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        {[
          ["Filings to fetch", fmtInt(preview.total)],
          ["ETA", `${fmtDuration(est.eta_seconds)} (${est.bound ?? ""})`],
          ["New storage", `~${est.new_storage_gb ?? 0} GB (${fmtInt(est.kept_filings)} kept)`],
          ["R2 cost", `~$${est.monthly_cost_usd ?? 0}/month (${est.total_storage_gb ?? 0} GB total)`],
        ].map(([label, value]) => (
          <div key={label}>
            <span className={EYEBROW}>{label}</span>
            <span className="text-sm text-[var(--2a-text)]">{value}</span>
          </div>
        ))}
      </div>
      <p className="mt-2 text-[11px] text-[var(--2a-text-muted)]">
        {fmtInt(est.requests)} SEC requests at {preview.params?.max_rps}/s, {preview.params?.concurrency} workers;
        peek {preview.peek_effective ? "on" : "off"}.
        {est.eta_seconds_at_measured_rate
          ? ` At the last run's measured ${est.measured_filings_per_second} filings/s: ${fmtDuration(est.eta_seconds_at_measured_rate)}.`
          : ""}
      </p>
      <div className="mt-3 overflow-x-auto">
        <table className="w-full text-xs">
          <thead className="border-b border-[var(--2a-border)]">
            <tr>
              <th className="px-2 py-1.5 text-left font-semibold text-[var(--2a-text-muted)]">Institution</th>
              {preview.years.map((y) => (
                <th key={y} className="px-2 py-1.5 text-right font-semibold text-[var(--2a-text-muted)]">
                  {y}
                </th>
              ))}
              <th className="px-2 py-1.5 text-right font-semibold text-[var(--2a-text-muted)]">Total</th>
            </tr>
          </thead>
          <tbody>
            {preview.institutions.map((inst) => (
              <tr key={inst.name} className="border-t border-[var(--2a-border)] align-top">
                <td className="px-2 py-1.5 text-[var(--2a-text)]">
                  {inst.name}
                  <div className="mt-0.5 text-[11px] text-[var(--2a-text-muted)]">
                    {inst.filers
                      .filter((f) => f.count > 0)
                      .map((f) => `${f.name} (CIK ${f.cik}): ${fmtInt(f.count)}`)
                      .join(" · ") || "no entity has filings in this range"}
                  </div>
                </td>
                {preview.years.map((y) => (
                  <td key={y} className="px-2 py-1.5 text-right tabular-nums text-[var(--2a-text)]">
                    {fmtInt(inst.years[y] ?? 0)}
                  </td>
                ))}
                <td className="px-2 py-1.5 text-right tabular-nums font-medium text-[var(--2a-text)]">
                  {fmtInt(inst.total)}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ─── Confirm dialog ─────────────────────────────────────────────────────────

function ConfirmDialog({ preview, targetLabel, onCancel, onConfirm, pending }) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-[rgba(15,23,42,0.25)]" role="dialog" aria-modal="true">
      <div className={`${CARD} w-[28rem] p-5`}>
        <h3 className="text-base font-semibold text-[var(--2a-navy)]">Queue this download?</h3>
        <dl className="mt-3 grid grid-cols-[8rem_1fr] gap-y-1 text-xs">
          <dt className="text-[var(--2a-text-muted)]">Institutions</dt>
          <dd className="text-[var(--2a-text)]">{preview.params.banks.join(", ")}</dd>
          <dt className="text-[var(--2a-text-muted)]">Filed</dt>
          <dd className="text-[var(--2a-text)]">
            {preview.params.from} – {preview.params.to}
          </dd>
          <dt className="text-[var(--2a-text-muted)]">Filings</dt>
          <dd className="text-[var(--2a-text)]">{fmtInt(preview.total)}</dd>
          <dt className="text-[var(--2a-text-muted)]">ETA</dt>
          <dd className="text-[var(--2a-text)]">{fmtDuration(preview.estimate?.eta_seconds)}</dd>
          <dt className="text-[var(--2a-text-muted)]">Storage</dt>
          <dd className="text-[var(--2a-text)]">
            ~{preview.estimate?.new_storage_gb} GB new, ~${preview.estimate?.monthly_cost_usd}/month
          </dd>
          <dt className="text-[var(--2a-text-muted)]">Run on</dt>
          <dd className="font-medium text-[var(--2a-text)]">{targetLabel}</dd>
        </dl>
        <div className="mt-4 flex justify-end gap-2">
          <button type="button" className={GHOST} onClick={onCancel} disabled={pending}>
            Back
          </button>
          <button type="button" className={BUTTON} onClick={onConfirm} disabled={pending}>
            Queue run
          </button>
        </div>
      </div>
    </div>
  );
}

// ─── Runs table ─────────────────────────────────────────────────────────────

function RunsTable({ runsPayload, pageIndex, onPage, loading, onAction, pendingAction, highlightId }) {
  const vocab = runsPayload?.vocabularies ?? {};
  const statuses = vocab.statuses ?? {};
  const targets = vocab.targets ?? {};
  const kindsLabel = vocab.kinds ?? {};
  const runKinds = vocab.run_kinds ?? {};
  const columns = [
    { field: "started_at", headerName: "Started", enableSorting: false, cell: (v, row) => fmtWhen(v || row.queued_at) },
    {
      field: "run_kind",
      headerName: "Institutions",
      enableSorting: false,
      cell: (v, row) => (v === "bulk_fetch" ? (row.params?.banks ?? []).join(", ") : runKinds[v] ?? v),
    },
    {
      field: "params",
      headerName: "Dates",
      enableSorting: false,
      cell: (p, row) =>
        row.run_kind === "bulk_fetch" ? `${p?.from ?? ""} – ${p?.to ?? ""}` : p?.to ? `through ${p.to}` : "through yesterday",
    },
    {
      field: "kinds",
      headerName: "Kinds",
      enableSorting: false,
      cell: (_v, row) => (row.params?.kinds ?? []).map((k) => kindsLabel[k] ?? k).join(", "),
    },
    { field: "target", headerName: "Target", enableSorting: false, cell: (v) => targets[v] ?? v ?? "" },
    {
      field: "display_status",
      headerName: "Status",
      enableSorting: false,
      cell: (v, row) => (
        <span style={v === "worker_lost" || v === "failed" ? { color: ERROR_INK } : undefined}>
          {statuses[v] ?? v}
          {row.stop_requested && row.status === "running" ? " (stopping)" : ""}
        </span>
      ),
    },
    {
      field: "done",
      headerName: "Progress",
      enableSorting: false,
      minWidth: 160,
      cell: (_v, row) =>
        row.run_kind === "bulk_fetch" ? (
          <div className="flex min-w-[9rem] items-center gap-2">
            <div className="h-1.5 flex-1 rounded bg-[var(--2a-bg)]">
              <div className="h-1.5 rounded bg-[var(--2a-gold)]" style={{ width: `${progressPercent(row)}%` }} />
            </div>
            <span className="tabular-nums text-[11px] text-[var(--2a-text-secondary)]">
              {fmtInt(row.done)}/{fmtInt(row.total)}
            </span>
          </div>
        ) : (
          row.progress?.new_filings !== undefined ? `${fmtInt(row.progress.new_filings)} new filings` : ""
        ),
    },
    {
      field: "rate_per_second",
      headerName: "Rate",
      enableSorting: false,
      align: "right",
      cell: (v) => (v ? `${v}/s` : ""),
    },
    {
      field: "eta_seconds",
      headerName: "ETA",
      enableSorting: false,
      cell: (v, row) => (row.status === "running" ? fmtDuration(v) : ""),
    },
    {
      field: "failed",
      headerName: "Failures",
      enableSorting: false,
      align: "right",
      cell: (v, row) => (
        <span title={(row.failures ?? []).map((f) => `${f.accession_number}: ${f.error}`).join("\n") || row.error || ""}>
          {fmtInt(v ?? 0)}
        </span>
      ),
    },
    {
      field: "_actions",
      headerName: "",
      enableSorting: false,
      cell: (_v, row) => (
        <span className="flex gap-1">
          {rowActions(row, runsPayload).map((a) => (
            <button
              key={a}
              type="button"
              className={GHOST}
              disabled={pendingAction === `${row.id}:${a}`}
              onClick={() => onAction(row.id, a)}
            >
              {a === "stop" ? "Stop" : a === "cancel" ? "Cancel" : "Requeue"}
            </button>
          ))}
        </span>
      ),
    },
  ];
  return (
    <DataGrid
      gridId="edgar-bulk-runs"
      columnDefs={columns}
      rowData={runsPayload?.rows ?? []}
      getRowId={(row) => row.id}
      selectedRowId={highlightId}
      getRowStyle={(row) => (highlightId && row.id === highlightId ? { background: HIGHLIGHT_BG } : undefined)}
      enableGlobalFilter={false}
      emptyMessage={loading ? "Loading…" : "No bulk downloads queued yet."}
      serverSide={{
        totalRows: runsPayload?.total ?? 0,
        pageIndex,
        pageSize: RUNS_PAGE_SIZE,
        sorting: [],
        loading,
        onSortingChange: () => {},
        onPageChange: onPage,
      }}
    />
  );
}

// ─── The tab ────────────────────────────────────────────────────────────────

export default function BulkDownloadTab() {
  const [inst, setInst] = useState(null);
  const [instError, setInstError] = useState(null);
  const [workers, setWorkers] = useState(null);
  const [runs, setRuns] = useState(null);
  const [runsPage, setRunsPage] = useState(0);
  const [runsLoading, startRuns] = useTransition();
  const [, startWorkers] = useTransition();
  const [checkingNow, startCheckNow] = useTransition();   // the dialog's "check again" only, not the 5 s poll

  const [banks, setBanks] = useState(() => new Set());
  const [preset, setPreset] = useState("2025-2026");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [kinds, setKinds] = useState(null);
  const [peek, setPeek] = useState(true);
  const [target, setTarget] = useState(null);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [concurrency, setConcurrency] = useState(6);
  const [maxRps, setMaxRps] = useState(8);

  // The newest preview response, tagged with the params it answers; the panel
  // shows "updating…" whenever the current params have no answer yet.
  const [previewResult, setPreviewResult] = useState({ key: null, payload: null, error: null });
  const [confirming, setConfirming] = useState(false);
  const [outcome, setOutcome] = useState(null);
  const [refreshOutcome, setRefreshOutcome] = useState(null);
  const [pendingAction, setPendingAction] = useState(null);
  const [pending, startTransition] = useTransition();
  const previewSeq = useRef(0);

  // "Run on your laptop" dialog: { source: "queue" | "steps", runId } or null.
  const [laptopDialog, setLaptopDialog] = useState(null);
  // Read once, lazily. It only renders inside the dialog, which is closed on the
  // first render, so a server render (no window -> false) cannot mismatch.
  const [hideWhenOnline, setHideWhenOnline] = useState(() => readHideWhenOnline(browserStorage()));
  const [highlightRunId, setHighlightRunId] = useState(null);
  const queueButtonRef = useRef(null);
  const stepsLinkRef = useRef(null);
  const openerRef = useRef(null);
  const runsCardRef = useRef(null);

  const vocab = inst?.vocabularies ?? {};
  const writable = canWrite(inst);
  const lastIndexed = inst?.last_indexed_date ?? null;

  // ── loads
  useEffect(() => {
    (async () => {
      const res = await loadBulkInstitutionsAction();
      if (!res.ok) {
        setInstError(res.error);
        return;
      }
      setInst(res.payload);
      const v = res.payload?.vocabularies ?? {};
      setKinds(new Set(v.default_kinds ?? []));
      setTarget(v.default_target ?? null);
      setConcurrency(v.default_concurrency ?? 6);
      setMaxRps(v.max_rps ?? 8);
      const range = presetRange("2025-2026", res.payload?.last_indexed_date);
      if (range) {
        setFrom(range.from);
        setTo(range.to);
      }
    })();
  }, []);

  const refreshWorkers = useCallback(() => {
    startWorkers(async () => {
      const res = await loadBulkWorkersAction();
      if (res.ok) setWorkers(res.payload);
    });
  }, []);

  const refreshRuns = useCallback((page) => {
    startRuns(async () => {
      const res = await loadBulkRunsAction({ page: page + 1, page_size: RUNS_PAGE_SIZE });
      if (res.ok) setRuns(res.payload);
    });
  }, []);

  useEffect(() => {
    refreshWorkers();
    refreshRuns(runsPage);
  }, [refreshWorkers, refreshRuns, runsPage]);

  // While the laptop dialog is open: check now, then every 5 s (independent of
  // the runs table's own refresh, which only runs while a run is active).
  const dialogOpen = laptopDialog !== null;
  useEffect(() => {
    if (!dialogOpen) return undefined;
    refreshWorkers();
    const id = setInterval(refreshWorkers, LAPTOP_DIALOG_RECHECK_MS);
    return () => clearInterval(id);
  }, [dialogOpen, refreshWorkers]);

  // Auto-refresh every 10 s while a run is queued or running (and the worker
  // dots with it).
  const active = runsActive(runs);
  useEffect(() => {
    if (!active) return undefined;
    const id = setInterval(() => {
      refreshRuns(runsPage);
      refreshWorkers();
    }, RUNS_REFRESH_MS);
    return () => clearInterval(id);
  }, [active, runsPage, refreshRuns, refreshWorkers]);

  // ── preview (debounced)
  const params = useMemo(
    () => buildParams({ banks, from, to, kinds: kinds ?? new Set(), peek, concurrency, maxRps }),
    [banks, from, to, kinds, peek, concurrency, maxRps],
  );
  const ready = Boolean(inst) && previewReady(params);
  const paramsKey = JSON.stringify(params);
  useEffect(() => {
    if (!ready) return undefined;
    const seq = ++previewSeq.current;
    const id = setTimeout(async () => {
      const res = await previewBulkAction(JSON.parse(paramsKey));
      if (seq !== previewSeq.current) return;
      setPreviewResult(
        res.ok ? { key: paramsKey, payload: res.payload, error: null } : { key: paramsKey, payload: null, error: res.error },
      );
    }, PREVIEW_DEBOUNCE_MS);
    return () => clearTimeout(id);
  }, [ready, paramsKey]);
  const previewLoading = ready && previewResult.key !== paramsKey;
  const preview = ready ? previewResult.payload : null;   // the last answer stays up while updating
  const previewError = ready && !previewLoading ? previewResult.error : null;

  function choosePreset(key) {
    setPreset(key);
    const range = presetRange(key, lastIndexed);
    if (range) {
      setFrom(range.from);
      setTo(range.to);
    }
  }

  function toggleBank(name) {
    setBanks((prev) => {
      const next = new Set(prev);
      if (next.has(name)) next.delete(name);
      else next.add(name);
      return next;
    });
  }

  function toggleKind(k) {
    setKinds((prev) => {
      const next = new Set(prev ?? []);
      if (next.has(k)) next.delete(k);
      else next.add(k);
      return next;
    });
  }

  function queue() {
    startTransition(async () => {
      const { outcome: o, dialog } = await queueAndOpen({
        queueRun: queueBulkRunAction, params, target, hideWhenOnline,
      });
      setConfirming(false);
      setOutcome(o);
      if (dialog) {
        openerRef.current = queueButtonRef.current;
        setLaptopDialog(dialog);
      }
      refreshRuns(0);
      setRunsPage(0);
      refreshWorkers();
    });
  }

  function checkWorkersNow() {
    startCheckNow(async () => {
      const res = await loadBulkWorkersAction();
      if (res.ok) setWorkers(res.payload);
    });
  }

  function openLaptopSteps() {
    openerRef.current = stepsLinkRef.current;
    setLaptopDialog(stepsDialog());
  }

  function closeLaptopDialog() {
    const opener = openerRef.current;
    setLaptopDialog(null);
    setTimeout(() => opener?.focus(), 0);
  }

  function viewQueuedRun() {
    const runId = laptopDialog?.runId ?? null;
    setLaptopDialog(null);
    setHighlightRunId(runId);
    setRunsPage(0);
    setTimeout(() => {
      runsCardRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
      runsCardRef.current?.focus({ preventScroll: true });
    }, 0);
  }

  function changeHideWhenOnline(value) {
    setHideWhenOnline(value);
    writeHideWhenOnline(browserStorage(), value);
  }

  function refreshIndex() {
    startTransition(async () => {
      const res = await queueRefreshIndexAction({ to: null, target });
      const o = queueOutcome(res);
      setRefreshOutcome(res.ok ? { ...o, text: o.text.replace("Queued.", "Index refresh queued.") } : o);
      refreshRuns(0);
      setRunsPage(0);
    });
  }

  function act(id, action) {
    setPendingAction(`${id}:${action}`);
    startTransition(async () => {
      const res = await bulkRunAction(id, action);
      setPendingAction(null);
      if (!res.ok) setOutcome({ error: true, text: res.error });
      refreshRuns(runsPage);
    });
  }

  if (instError) {
    return (
      <div className={`${CARD} mt-4 p-6 text-xs`} style={{ color: ERROR_INK }}>
        Could not load institutions: {instError}
      </div>
    );
  }
  if (!inst) {
    return <div className={`${CARD} mt-4 p-6 text-xs text-[var(--2a-text-muted)]`}>Loading…</div>;
  }

  const laptop = laptopStatus(workers);
  const render = renderStatus(workers);
  const targets = vocab.targets ?? {};
  const kindOptions = vocab.kinds ?? {};

  return (
    <div className="mt-4 flex flex-col gap-4">
      <div className="grid gap-4 xl:grid-cols-[3fr_2fr]">
        <InstitutionPicker
          rows={inst.rows ?? []}
          selected={banks}
          onToggle={toggleBank}
          onAll={(names) => setBanks(new Set(names))}
          onNone={() => setBanks(new Set())}
        />

        <div className="flex flex-col gap-4">
          <div className={`${CARD} p-4`}>
            <h3 className="mb-2 text-sm font-semibold text-[var(--2a-navy)]">Time frame</h3>
            <div className="mb-2 flex flex-wrap gap-2">
              {PRESETS.map((p) => (
                <button
                  key={p.key}
                  type="button"
                  className={`${GHOST} ${preset === p.key ? "border-[var(--2a-gold)] font-semibold" : ""}`}
                  onClick={() => choosePreset(p.key)}
                >
                  {p.label}
                </button>
              ))}
            </div>
            <div className="flex flex-wrap items-end gap-3">
              <label className="flex flex-col gap-1">
                <span className={EYEBROW}>Filed from</span>
                <input
                  type="date"
                  className={CONTROL}
                  value={from}
                  max={lastIndexed ?? undefined}
                  onChange={(e) => {
                    setPreset("custom");
                    setFrom(e.target.value);
                  }}
                />
              </label>
              <label className="flex flex-col gap-1">
                <span className={EYEBROW}>Filed to</span>
                <input
                  type="date"
                  className={CONTROL}
                  value={to}
                  onChange={(e) => {
                    setPreset("custom");
                    setTo(e.target.value);
                  }}
                />
              </label>
            </div>
            <div className="mt-2 flex flex-wrap items-center gap-2 text-[11px] text-[var(--2a-text-muted)]">
              <span>Index loaded through {lastIndexed ?? "—"}.</span>
              {writable && (
                <button type="button" className={GHOST} onClick={refreshIndex} disabled={pending}>
                  Refresh index
                </button>
              )}
            </div>
            {refreshOutcome && (
              <p className="mt-1 text-[11px]" style={refreshOutcome.error ? { color: ERROR_INK } : undefined}>
                {refreshOutcome.text} <Command text={refreshOutcome.command} />
              </p>
            )}
          </div>

          <div className={`${CARD} p-4`}>
            <h3 className="mb-2 text-sm font-semibold text-[var(--2a-navy)]">Document kinds</h3>
            <div className="flex flex-col gap-1.5 text-xs text-[var(--2a-text)]">
              {Object.entries(kindOptions).map(([k, label]) => (
                <label key={k} className="flex items-center gap-2">
                  <input type="checkbox" checked={kinds?.has(k) ?? false} onChange={() => toggleKind(k)} />
                  {label}
                </label>
              ))}
              <label className="mt-1 flex items-center gap-2 border-t border-[var(--2a-border)] pt-2">
                <input type="checkbox" checked={peek} onChange={(e) => setPeek(e.target.checked)} />
                Skip preliminaries by peeking
                <span className="text-[11px] text-[var(--2a-text-muted)]">(reads the first 16 KB before downloading)</span>
              </label>
            </div>
          </div>

          <div className={`${CARD} p-4`}>
            <h3 className="mb-2 text-sm font-semibold text-[var(--2a-navy)]">Run on</h3>
            <div className="flex flex-col gap-2 text-xs" role="radiogroup" aria-label="Run on">
              <div className="flex flex-wrap items-center gap-2">
                <label className="flex flex-wrap items-center gap-2">
                  <input
                    type="radio"
                    name="bulk-run-on"
                    value="laptop"
                    checked={target === "laptop"}
                    onChange={() => setTarget("laptop")}
                  />
                  <span className="font-medium text-[var(--2a-text)]">{targets.laptop}</span>
                  <Dot on={laptop.online} />
                  <span className="text-[var(--2a-text-secondary)]">{laptop.text}</span>
                  <Command text={laptop.command} />
                </label>
                <button
                  ref={stepsLinkRef}
                  type="button"
                  className="text-[11px] text-[var(--2a-navy)] underline decoration-[var(--2a-gold)] underline-offset-2"
                  onClick={openLaptopSteps}
                >
                  Show these steps
                </button>
              </div>
              <label className={`flex flex-wrap items-center gap-2 ${render.greyed ? "opacity-60" : ""}`}>
                <input
                  type="radio"
                  name="bulk-run-on"
                  value="render"
                  checked={target === "render"}
                  onChange={() => setTarget("render")}
                />
                <span className="font-medium text-[var(--2a-text)]">{targets.render}</span>
                <Dot on={render.state === "online"} />
                <span className="text-[var(--2a-text-secondary)]">{render.text}</span>
              </label>
            </div>
            <button
              type="button"
              className="mt-3 text-[11px] text-[var(--2a-navy)] underline decoration-[var(--2a-gold)] underline-offset-2"
              onClick={() => setShowAdvanced((s) => !s)}
              aria-expanded={showAdvanced}
            >
              {showAdvanced ? "Hide advanced" : "Advanced"}
            </button>
            {showAdvanced && (
              <div className="mt-2 flex flex-wrap gap-3">
                <label className="flex flex-col gap-1">
                  <span className={EYEBROW}>Concurrency</span>
                  <input
                    type="number"
                    min={1}
                    max={vocab.max_concurrency ?? 16}
                    className={`${CONTROL} w-24`}
                    value={concurrency}
                    onChange={(e) => setConcurrency(e.target.value)}
                  />
                </label>
                <label className="flex flex-col gap-1">
                  <span className={EYEBROW}>Max requests / second</span>
                  <input
                    type="number"
                    min={1}
                    max={vocab.max_rps ?? 8}
                    className={`${CONTROL} w-24`}
                    value={maxRps}
                    onChange={(e) => setMaxRps(Math.min(Number(e.target.value) || 1, vocab.max_rps ?? 8))}
                  />
                </label>
                <p className="w-full text-[11px] text-[var(--2a-text-muted)]">
                  {vocab.max_rps ?? 8}/s is the ceiling across every worker; all of a run&apos;s workers share one limiter.
                </p>
              </div>
            )}
          </div>
        </div>
      </div>

      <PreviewPanel preview={preview} loading={previewLoading} error={previewError} />

      {writable && (
        <div className={`${CARD} flex flex-wrap items-center gap-3 p-4`}>
          <button
            ref={queueButtonRef}
            type="button"
            className={BUTTON}
            disabled={pending || !preview || !preview.total || !target}
            onClick={() => setConfirming(true)}
          >
            Queue run
          </button>
          <span className="text-xs text-[var(--2a-text-muted)]">
            The download runs in a worker, not in the app — it can be stopped and requeued below.
          </span>
          {outcome && (
            <div className="w-full text-xs" style={outcome.error ? { color: ERROR_INK } : undefined}>
              {outcome.text} <Command text={outcome.command} />
              {outcome.setupAnchor && (
                <a
                  href={`#${outcome.setupAnchor}`}
                  className="ml-1 text-[var(--2a-navy)] underline decoration-[var(--2a-gold)] underline-offset-2"
                >
                  Setup steps
                </a>
              )}
              {outcome.lastIndexed && <span> Last indexed date: {outcome.lastIndexed}.</span>}
            </div>
          )}
        </div>
      )}

      {confirming && preview && (
        <ConfirmDialog
          preview={preview}
          targetLabel={targets[target] ?? target}
          pending={pending}
          onCancel={() => setConfirming(false)}
          onConfirm={queue}
        />
      )}

      {laptopDialog && (
        <LaptopWorkerDialog
          dialog={laptopDialog}
          workers={workers}
          checking={checkingNow}
          onCheck={checkWorkersNow}
          onViewRun={viewQueuedRun}
          onClose={closeLaptopDialog}
          hideWhenOnline={hideWhenOnline}
          onHideChange={changeHideWhenOnline}
        />
      )}

      <div ref={runsCardRef} tabIndex={-1} className={`${CARD} p-4 focus:outline-none`}>
        <div className="mb-2 flex items-center justify-between">
          <h3 className="text-sm font-semibold text-[var(--2a-navy)]">Bulk downloads</h3>
          <span className="text-[11px] text-[var(--2a-text-muted)]">
            {active ? "Refreshing every 10 s while a run is active." : ""}
          </span>
          <button type="button" className={GHOST} onClick={() => refreshRuns(runsPage)} disabled={runsLoading}>
            Refresh
          </button>
        </div>
        <RunsTable
          runsPayload={runs}
          pageIndex={runsPage}
          onPage={setRunsPage}
          loading={runsLoading}
          onAction={act}
          pendingAction={pendingAction}
          highlightId={highlightRunId}
        />
      </div>

      <div id={workers?.render?.setup_anchor ?? "render-worker-setup"} className={`${CARD} p-4`}>
        <h3 className="mb-1 text-sm font-semibold text-[var(--2a-navy)]">Render worker setup</h3>
        <p className="mb-2 text-xs text-[var(--2a-text-muted)]">
          {workers?.render?.reason ?? ""} {vocab.render_refusal ? `— ${vocab.render_refusal}.` : ""}
        </p>
        <ol className="list-decimal pl-5 text-xs text-[var(--2a-text-secondary)]">
          {(workers?.render?.setup_steps ?? []).map((s) => (
            <li key={s} className="py-0.5">
              {s}
            </li>
          ))}
        </ol>
      </div>
    </div>
  );
}
