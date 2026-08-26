"use client";

/**
 * WorkflowTriggerScheduler — the Triggers management screen (schedulerux).
 *
 * WHAT THIS REPLACED, AND WHY IT WAS A REPLACEMENT AND NOT AN EXTENSION
 * ─────────────────────────────────────────────────────────────────────────
 * The previous version of this file was a hand-rolled four-column `<table>`
 * with one write on it: a create form hardcoded to `document_confirmed`. It
 * rendered `schedule_cron` raw and showed none of `occurrence_count`,
 * `last_fired_at`, `timezone`, `start_date`, `end_date` or `max_occurrences` —
 * every one of which is a real, live column the scheduler reads on every tick.
 * There was no sort, no filter, no detail pane, no permission envelope and no
 * way to pause, edit or delete anything. Extending it would have meant
 * rewriting each of its lines, so it was rewritten. The PATH is kept because
 * `verify_schedulercore.py` reads this file to assert the deployed vocabulary
 * really is `'scheduled'`.
 *
 * BUILT ON THE EXISTING GRID
 * ─────────────────────────────────────────────────────────────────────────
 * `components/ui/DataGrid.jsx` already owns sort, per-column filter, global
 * filter, column visibility, column reorder and pagination — the same
 * component the Positions, Transactions and Securities screens use. Nothing
 * here re-implements any of it. The paused-row treatment goes through
 * `getRowStyle`, the row-level hook DataGrid already exposes, because "this
 * whole row is a different kind of thing" is a row fact and a per-cell renderer
 * can only mark the cell it owns.
 *
 * WHAT THE SERVER DECIDES AND THIS FILE ONLY RENDERS
 * ─────────────────────────────────────────────────────────────────────────
 *   · `permissions.can_write`  — whether ANY write control exists. No local
 *     default, no `?? true`: a missing envelope fails closed. It is not the
 *     enforcement either; every endpoint re-checks, and verify_schedulerux
 *     asserts the two independently, because a hidden control over an open
 *     endpoint and a gated endpoint under a visible button are both real bugs
 *     and neither is ruled out by testing the other.
 *   · `schedule_summary`       — "Daily at 9:00 AM (America/New_York)", built
 *     server-side from the same parse_cron the evaluator uses. A cron-to-English
 *     renderer in the browser would be a second opinion about what a schedule
 *     means, and the browser's is the one the operator reads while the server's
 *     is the one that runs.
 *   · `next_occurrence`        — from the scheduler's own recurrence engine.
 *   · every 422 message        — surfaced verbatim, never re-derived.
 */

import { useCallback, useEffect, useMemo, useState } from "react";

import DataGrid from "@/components/ui/DataGrid";
import TriggerDetailPane from "@/components/admin/TriggerDetailPane";
import { formatDateTime, statusPillClass } from "@/lib/workflowFormat";

const CARD = { borderColor: "#ece8dd", boxShadow: "0 1px 3px rgba(0,0,0,0.06)" };

export default function WorkflowTriggerScheduler({
  initialRows = [],
  initialPermissions = null,
  workflows = [],
}) {
  const [rows, setRows] = useState(initialRows);
  // NO FALLBACK. `can_write` is false unless the server said otherwise.
  const [permissions, setPermissions] = useState(
    initialPermissions || { can_read: true, can_write: false },
  );
  const [selectedId, setSelectedId] = useState(null);
  const [mode, setMode] = useState("read");
  const [loadError, setLoadError] = useState(null);

  const canWrite = !!permissions?.can_write;

  // The live read. The screen is seeded by the server component so the first
  // paint is real data, then every mutation re-reads through this — the row the
  // API returns from a PATCH is authoritative for that row, but occurrence
  // counters on OTHER rows can have moved in the meantime if a tick ran.
  const reload = useCallback(async () => {
    try {
      const res = await fetch("/api/admin/workflow-triggers", {
        cache: "no-store",
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        setLoadError(
          typeof data.error === "string" ? data.error : "Could not load triggers.",
        );
        return;
      }
      setRows(data.rows || []);
      if (data.permissions) setPermissions(data.permissions);
      setLoadError(null);
    } catch (e) {
      setLoadError(e.message);
    }
  }, []);

  const selected = useMemo(
    () => rows.find((r) => String(r.id) === String(selectedId)) || null,
    [rows, selectedId],
  );

  // A selected row that has just been deleted, or vanished from someone else's
  // delete, must not leave the pane rendering a stale copy.
  useEffect(() => {
    if (selectedId && !selected && mode !== "create") {
      setSelectedId(null);
      setMode("read");
    }
  }, [selectedId, selected, mode]);

  const columnDefs = useMemo(
    () => [
      {
        field: "workflow_name",
        headerName: "Workflow",
        enableColumnFilter: true,
        filterPlaceholder: "Workflow…",
        cell: (value, row) => (
          <span
            className={
              row.is_active
                ? "font-medium text-[var(--2a-navy)]"
                : "font-medium text-[var(--2a-text-muted)]"
            }
          >
            {value || "—"}
          </span>
        ),
      },
      {
        field: "trigger_type",
        headerName: "Type",
        enableColumnFilter: true,
        filterPlaceholder: "Type…",
      },
      {
        field: "schedule_summary",
        headerName: "Recurrence",
        enableColumnFilter: true,
        filterPlaceholder: "Recurrence…",
        minWidth: 220,
        cell: (value, row) => (
          <span title={row.schedule_cron || row.event_type || ""}>
            {value || "—"}
            {row.schedule_error ? (
              <span className="ml-1 text-[var(--2a-gold)]">
                (unrunnable: {row.schedule_error})
              </span>
            ) : null}
          </span>
        ),
      },
      {
        field: "is_active",
        headerName: "State",
        align: "center",
        cell: (value) => (
          <span className={statusPillClass(value ? "active" : "pending")}>
            {value ? "Active" : "Paused"}
          </span>
        ),
      },
      {
        field: "occurrence_count",
        headerName: "Fired",
        align: "right",
        cell: (value) => value ?? 0,
      },
      {
        field: "last_fired_at",
        headerName: "Last fired",
        cell: (value) => formatDateTime(value),
      },
      {
        field: "next_occurrence",
        headerName: "Next",
        cell: (value, row) =>
          row.is_active ? formatDateTime(value) : (
            <span className="text-[var(--2a-text-muted)]">— paused</span>
          ),
      },
    ],
    [],
  );

  // A paused trigger has to be recognisable at a glance from across the grid,
  // not only by reading its pill. Muted ink plus a cream wash — no red, no
  // strikethrough: paused is a deliberate operating state, not an error.
  const getRowStyle = useCallback(
    (row) =>
      row.is_active
        ? undefined
        : { background: "var(--2a-bg)", opacity: 0.72 },
    [],
  );

  const active = rows.filter((r) => r.is_active).length;
  const paused = rows.length - active;

  function handleSaved(saved, savedMode) {
    if (savedMode === "create" && saved?.id) setSelectedId(String(saved.id));
    else if (saved?.id) setSelectedId(String(saved.id));
    setMode("read");
    reload();
  }

  function handleDeleted() {
    setSelectedId(null);
    setMode("read");
    reload();
  }

  return (
    <div className="mt-6 grid gap-4 lg:grid-cols-[minmax(0,1fr)_22rem]">
      <div className="rounded-lg border bg-white p-4" style={CARD}>
        <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
          <p className="text-xs text-[var(--2a-text-muted)]">
            {rows.length} trigger{rows.length === 1 ? "" : "s"} — {active}{" "}
            active, {paused} paused
          </p>
          {canWrite ? (
            <button
              type="button"
              onClick={() => {
                setSelectedId(null);
                setMode("create");
              }}
              className="rounded bg-[var(--2a-navy)] px-3 py-1.5 text-xs font-medium text-white"
            >
              New trigger
            </button>
          ) : (
            <span className="text-[10px] uppercase tracking-[0.12em] text-[var(--2a-text-muted)]">
              View only
            </span>
          )}
        </div>

        {loadError && (
          <p className="mb-2 text-xs" style={{ color: "#9B2335" }}>
            {loadError}
          </p>
        )}

        <DataGrid
          gridId="workflow-triggers"
          columnDefs={columnDefs}
          rowData={rows}
          getRowId={(row) => String(row.id)}
          selectedRowId={selectedId}
          onRowClick={(row) => {
            setSelectedId(String(row.id));
            setMode("read");
          }}
          getRowStyle={getRowStyle}
          quickFilterPlaceholder="Search triggers…"
          emptyMessage="No triggers configured."
        />
      </div>

      <TriggerDetailPane
        mode={mode}
        trigger={selected}
        workflows={workflows}
        canWrite={canWrite}
        onModeChange={setMode}
        onSaved={handleSaved}
        onDeleted={handleDeleted}
        onCancel={() => setMode("read")}
      />
    </div>
  );
}
