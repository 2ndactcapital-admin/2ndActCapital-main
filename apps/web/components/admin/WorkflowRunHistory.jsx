"use client";

/**
 * WorkflowRunHistory — the Run History screen (schedulerhistory).
 *
 * WHAT THIS REPLACED
 * ─────────────────────────────────────────────────────────────────────────
 * A hand-rolled four-column `<table>` (workflow / status / started by /
 * started) with no sort, no filter, no detail pane and no permission envelope,
 * whose "Started by" column printed an em-dash for every run the SCHEDULER
 * started — because a scheduled run records no human starter, and the old
 * column only knew how to look one up. Since the scheduler sprint those runs
 * are real and regular, so the column was wrong about the majority of rows it
 * was about to start showing. The PATH is kept and the screen rewritten in
 * place; there is exactly one Run History screen and one run detail renderer.
 *
 * WHAT THE SERVER DECIDES AND THIS FILE ONLY RENDERS
 * ─────────────────────────────────────────────────────────────────────────
 *   · `origin.kind`          — 'scheduled' or 'manual', read from the run's own
 *     stored context (the tick's stamp), never inferred here from a null
 *     `started_by`.
 *   · `started_by_label`     — the exact string the column prints, including
 *     the "Scheduled: …" form. The browser does not assemble it.
 *   · `origin.schedule_summary` — built by the same `describe_schedule` the
 *     firing loop uses, so this screen and the Triggers screen cannot come to
 *     two different opinions about what one schedule means.
 *   · `duration_seconds`     — and, per step, `duration_measured`. A false
 *     there prints NOT_MEASURED, not a zero.
 *   · every filter           — status and time window are applied in SQL. The
 *     grid's own per-column filters still work on top, but they narrow the page
 *     the server sent, which is a different claim and is not what the filter
 *     bar drives.
 *   · `permissions.can_read` — no local default. Run History is read-only end
 *     to end, so `can_write` is a constant false server-side rather than a
 *     capability, and there is no write control on this screen to gate.
 *
 * THE FILTER BAR CALLS THE API. It does not filter `rows`. That is deliberate
 * and it is the only honest option: the list is capped server-side, so
 * "completed runs in the last 24 hours" filtered in the browser would mean
 * "…among the most recent 200 runs of any status", which is a different and
 * quietly wrong answer.
 */

import { useCallback, useEffect, useMemo, useState } from "react";

import DataGrid from "@/components/ui/DataGrid";
import RunDetailPane from "@/components/admin/RunDetailPane";
import {
  formatDateTime,
  formatDuration,
  statusPillClass,
  NOT_MEASURED,
  NOT_MEASURED_WHY,
} from "@/lib/workflowFormat";

const CARD = { borderColor: "#ece8dd", boxShadow: "0 1px 3px rgba(0,0,0,0.06)" };
const CONTROL =
  "rounded border border-[var(--2a-border)] bg-white px-2 py-1.5 text-xs text-[var(--2a-text)] focus:outline-none focus:ring-1 focus:ring-[var(--2a-gold)]";
const EYEBROW =
  "block text-[10px] font-semibold uppercase tracking-[0.12em] text-[var(--2a-text-muted)]";

// The period names the API resolves. Named here only to LABEL the options; the
// boundary each one means is computed server-side against the server's clock,
// and the value posted is the name, never a timestamp.
const PERIOD_LABELS = {
  "24h": "Last 24 hours",
  "7d": "Last 7 days",
  "30d": "Last 30 days",
  "90d": "Last 90 days",
  all: "All time",
};

// A run's origin decides how its "Started by" cell reads. Only the ACCENT is
// decided here — the text itself is the server's `started_by_label`.
function StartedBy({ row }) {
  const scheduled = row.origin?.kind === "scheduled";
  return (
    <span
      className={
        scheduled
          ? "text-[var(--2a-navy)]"
          : "text-[var(--2a-text-secondary)]"
      }
      title={
        scheduled
          ? `Trigger ${row.origin?.trigger_id || "—"}`
          : row.started_by_email || ""
      }
    >
      {row.started_by_label || "—"}
    </span>
  );
}

export default function WorkflowRunHistory({
  initialRows = [],
  initialPermissions = null,
  initialFilters = null,
  initialSelectedId = null,
}) {
  const [rows, setRows] = useState(initialRows);
  // NO FALLBACK. A missing envelope fails closed, exactly as the Triggers
  // screen does — this screen has no write control to hide, but a screen that
  // invented `{can_read: true}` when the server sent nothing is how a lost
  // envelope starts reading as permission.
  const [permissions, setPermissions] = useState(
    initialPermissions || { can_read: false, can_write: false },
  );
  const [appliedFilters, setAppliedFilters] = useState(initialFilters || null);
  const [status, setStatus] = useState(initialFilters?.status?.[0] || "");
  const [period, setPeriod] = useState(initialFilters?.period || "all");
  const [selectedId, setSelectedId] = useState(
    initialSelectedId ? String(initialSelectedId) : null,
  );
  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState(null);

  const statuses = permissions?.statuses || [];
  const periods = permissions?.periods || [];

  // The live read. The screen is seeded by the server component so the first
  // paint is real data; every filter change re-reads through here so the SERVER
  // decides which runs match, over the whole table rather than over this page.
  const reload = useCallback(
    async (nextStatus, nextPeriod) => {
      setLoading(true);
      try {
        const query = new URLSearchParams();
        if (nextStatus) query.set("status", nextStatus);
        if (nextPeriod) query.set("period", nextPeriod);
        const res = await fetch(
          `/api/admin/workflow-runs${query.toString() ? `?${query}` : ""}`,
          { cache: "no-store" },
        );
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
          setLoadError(
            typeof data.error === "string" ? data.error : "Could not load runs.",
          );
          return;
        }
        setRows(data.rows || []);
        if (data.permissions) setPermissions(data.permissions);
        setAppliedFilters(data.filters || null);
        setLoadError(null);
      } catch (e) {
        setLoadError(e.message);
      } finally {
        setLoading(false);
      }
    },
    [],
  );

  const selected = useMemo(
    () => rows.find((r) => String(r.id) === String(selectedId)) || null,
    [rows, selectedId],
  );

  // A selected run that has dropped out of the current filter must not leave
  // the pane rendering a row the list no longer contains.
  useEffect(() => {
    if (selectedId && !selected) setSelectedId(null);
  }, [selectedId, selected]);

  const columnDefs = useMemo(
    () => [
      {
        field: "workflow_name",
        headerName: "Workflow",
        enableColumnFilter: true,
        filterPlaceholder: "Workflow…",
        minWidth: 200,
        cell: (value, row) => (
          <span className="font-medium text-[var(--2a-navy)]">
            {value || "—"}
            <span className="ml-2 text-[10px] font-normal text-[var(--2a-text-muted)]">
              v{row.version_number}
            </span>
          </span>
        ),
      },
      {
        field: "status",
        headerName: "Status",
        align: "center",
        cell: (value) => <span className={statusPillClass(value)}>{value}</span>,
      },
      {
        field: "started_by_label",
        headerName: "Started by",
        enableColumnFilter: true,
        filterPlaceholder: "Started by…",
        minWidth: 220,
        cell: (_value, row) => <StartedBy row={row} />,
      },
      {
        field: "started_at",
        headerName: "Started",
        cell: (value) => formatDateTime(value),
      },
      {
        field: "completed_at",
        headerName: "Completed",
        cell: (value, row) =>
          value ? (
            formatDateTime(value)
          ) : (
            <span className="text-[var(--2a-text-muted)]">
              {row.status === "held" ? "— held" : "— in progress"}
            </span>
          ),
      },
      {
        field: "duration_seconds",
        headerName: "Duration",
        align: "right",
        // NOT a plain formatDuration. A run that finished inside its own start
        // call was inserted on one connection and completed on another whose
        // transaction opened first, so its interval is negative and means
        // nothing — the server sends duration_measured: false for those and
        // this column says so rather than printing a number.
        cell: (value, row) =>
          row.duration_measured ? (
            <span className="text-[var(--2a-text-secondary)]">
              {formatDuration(value)}
            </span>
          ) : row.completed_at ? (
            <span
              className="italic text-[var(--2a-text-muted)]"
              title={NOT_MEASURED_WHY}
            >
              {NOT_MEASURED}
            </span>
          ) : (
            <span className="text-[var(--2a-text-muted)]">—</span>
          ),
      },
    ],
    [],
  );

  // A held run is an operational problem and has to be findable at a glance
  // from across the grid, not only by reading its pill.
  const getRowStyle = useCallback(
    (row) =>
      row.status === "held"
        ? { background: "rgba(232,213,163,0.14)" }
        : undefined,
    [],
  );

  function applyStatus(next) {
    setStatus(next);
    setSelectedId(null);
    reload(next, period);
  }

  function applyPeriod(next) {
    setPeriod(next);
    setSelectedId(null);
    reload(status, next);
  }

  const scheduled = rows.filter((r) => r.origin?.kind === "scheduled").length;
  const held = rows.filter((r) => r.status === "held").length;

  return (
    <div className="mt-6 grid gap-4 lg:grid-cols-[minmax(0,1fr)_24rem]">
      <div className="rounded-lg border bg-white p-4" style={CARD}>
        <div className="mb-3 flex flex-wrap items-end gap-4">
          <label className="flex flex-col gap-1">
            <span className={EYEBROW}>Status</span>
            <select
              className={CONTROL}
              value={status}
              disabled={loading}
              onChange={(e) => applyStatus(e.target.value)}
            >
              <option value="">All statuses</option>
              {statuses.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </select>
          </label>

          <label className="flex flex-col gap-1">
            <span className={EYEBROW}>Period</span>
            <select
              className={CONTROL}
              value={period}
              disabled={loading}
              onChange={(e) => applyPeriod(e.target.value)}
            >
              {periods.map((p) => (
                <option key={p} value={p}>
                  {PERIOD_LABELS[p] || p}
                </option>
              ))}
            </select>
          </label>

          <p className="ml-auto text-xs text-[var(--2a-text-muted)]">
            {loading
              ? "Loading…"
              : `${rows.length} run${rows.length === 1 ? "" : "s"} — ${scheduled} started by a schedule, ${held} held`}
          </p>
        </div>

        {appliedFilters?.since && (
          <p className="mb-2 text-[11px] text-[var(--2a-text-muted)]">
            Showing runs started on or after{" "}
            {formatDateTime(appliedFilters.since)} — the window the server
            applied, not one computed here.
          </p>
        )}

        {loadError && (
          <p className="mb-2 text-xs" style={{ color: "#9B2335" }}>
            {loadError}
          </p>
        )}

        <DataGrid
          gridId="workflow-run-history"
          columnDefs={columnDefs}
          rowData={rows}
          getRowId={(row) => String(row.id)}
          selectedRowId={selectedId}
          onRowClick={(row) => setSelectedId(String(row.id))}
          getRowStyle={getRowStyle}
          quickFilterPlaceholder="Search runs…"
          emptyMessage="No workflow runs match this filter."
        />
      </div>

      <RunDetailPane runId={selected ? String(selected.id) : null} row={selected} />
    </div>
  );
}
