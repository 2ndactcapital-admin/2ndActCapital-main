"use client";

/**
 * NoteExtractionResults — the evaluation harness's results grid (noteextractb1).
 *
 * Runs list (evaluation + pilot) through the shared DataGrid; selecting an
 * evaluation run shows one row per candidate model — accuracy, missed-value
 * rate, invented-value rate, cost per note, latency, cache share — plus the
 * trimming recall, EdgarTools / rules coverage, skip-second-reader, Jev and
 * the distribution tally. Read-only: the harness REPORTS, it never picks
 * models (permissions.can_write is false and nothing here writes).
 */

import { useMemo, useState, useTransition } from "react";

import DataGrid from "@/components/ui/DataGrid";
import { loadRunAction } from "@/lib/noteExtractionActions";

const CARD = "rounded-md border border-[var(--2a-border)] bg-[var(--2a-bg-card)]";
const EYEBROW =
  "block text-[10px] font-semibold uppercase tracking-[0.12em] text-[var(--2a-text-muted)]";

const pct = (v) => (v === null || v === undefined ? "—" : `${(v * 100).toFixed(1)}%`);
const usd = (v) => (v === null || v === undefined ? "—" : `$${Number(v).toFixed(5)}`);

const RUN_COLUMNS = [
  { field: "run_kind", headerName: "Kind" },
  { field: "status", headerName: "Status" },
  { field: "started_at", headerName: "Started" },
  { field: "notes_done", headerName: "Notes" },
  { field: "spent_usd", headerName: "Spent", cell: (v) => usd(v) },
  { field: "spend_cap_usd", headerName: "Cap", cell: (v) => usd(v) },
  { field: "stop_reason", headerName: "Stop reason" },
];

const CANDIDATE_COLUMNS = [
  { field: "candidate", headerName: "Candidate" },
  { field: "accuracy", headerName: "Accuracy", cell: (v) => pct(v) },
  { field: "null_rate", headerName: "Missed", cell: (v) => pct(v) },
  { field: "invented_rate", headerName: "Invented", cell: (v) => pct(v) },
  { field: "dist_accuracy", headerName: "Distribution + fees", cell: (v) => pct(v) },
  { field: "cost_per_note", headerName: "Cost / note", cell: (v) => usd(v) },
  { field: "latency", headerName: "Median latency (ms)" },
  { field: "cache_share", headerName: "Cached prompt", cell: (v) => pct(v) },
];

export default function NoteExtractionResults({ initial }) {
  const runs = Array.isArray(initial?.rows) ? initial.rows : [];
  const [run, setRun] = useState(null);
  const [error, setError] = useState(null);
  const [pending, startTransition] = useTransition();

  const open = (row) => {
    startTransition(async () => {
      const res = await loadRunAction(row.id);
      if (res.ok) {
        setRun(res.payload.run);
        setError(null);
      } else setError(res.error);
    });
  };

  const report = run?.report ?? null;
  const candidateRows = useMemo(
    () =>
      Object.entries(report?.candidates ?? {}).map(([name, c]) => ({
        id: name,
        candidate: name,
        accuracy: c.fields?.overall?.accuracy,
        null_rate: c.fields?.overall?.null_rate,
        invented_rate: c.fields?.overall?.invented_rate,
        dist_accuracy: c.distribution_and_fees?.accuracy,
        cost_per_note: c.calls?.cost_per_note,
        latency: c.calls?.latency_ms_median,
        cache_share: c.calls?.cache_share,
      })),
    [report],
  );

  return (
    <div className="mt-6 space-y-4">
      <div className={CARD}>
        <DataGrid
          gridId="note-extraction-runs"
          columnDefs={RUN_COLUMNS}
          rowData={runs}
          getRowId={(r) => r.id}
          onRowClick={open}
          selectedRowId={run?.id}
          emptyMessage="No evaluation or pilot runs yet."
          pageSize={20}
        />
      </div>
      {error && <p className="text-xs text-[#9B2335]">{error}</p>}
      {pending && <p className="text-xs text-[var(--2a-text-muted)]">Loading…</p>}
      {report && (
        <>
          {candidateRows.length > 0 && (
            <div className={CARD}>
              <div className="px-3 py-2">
                <span className={EYEBROW}>Candidates on the gold set ({report.gold_notes} notes)</span>
              </div>
              <DataGrid
                gridId="note-extraction-candidates"
                columnDefs={CANDIDATE_COLUMNS}
                rowData={candidateRows}
                getRowId={(r) => r.id}
                enablePagination={false}
              />
            </div>
          )}
          <div className={`${CARD} p-3`}>
            <span className={EYEBROW}>Report</span>
            <pre className="mt-2 max-h-[60vh] overflow-auto whitespace-pre-wrap text-xs text-[var(--2a-text)]">
              {JSON.stringify(
                Object.fromEntries(Object.entries(report).filter(([k]) => k !== "candidates")),
                null,
                2,
              )}
            </pre>
          </div>
        </>
      )}
    </div>
  );
}
