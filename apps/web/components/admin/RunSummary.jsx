"use client";

import { capNote, runSummaryItems } from "@/lib/edgarBulkDownload.mjs";

// EDGAROPS1 — one bulk run's summary in the "Bulk downloads" table: requested,
// fetched, stored, skipped (reasons in the tooltip), failed, carried over,
// duration — the field list and its labels from the envelope
// (vocabularies.summary_fields). A scheduled download that hit its daily cap
// says "cap reached — N carried over"; one waiting for a worker shows why. A run
// with no summary (an index refresh, an old run) shows "—". Split out of
// EdgarBulkDownload.jsx (which imports server actions) so node tests render it.
const MUTED = "text-[11px] text-[var(--2a-text-muted)]";

export default function RunSummary({ run, vocab }) {
  const items = runSummaryItems(run, vocab);
  const cap = capNote(run);
  const waiting = run?.display_status === "waiting" ? run?.auto_download?.waiting : null;
  return (
    <div className="min-w-[14rem] text-[11px] leading-snug" data-testid="run-summary">
      {items ? (
        <span className="flex flex-wrap gap-x-2 text-[var(--2a-text-secondary)]">
          {items.map((it) => (
            <span key={it.key} title={it.title} data-summary={it.key}>
              <span className="text-[var(--2a-text-muted)]">{it.label}</span>{" "}
              <span className="tabular-nums text-[var(--2a-text)]">{it.text}</span>
            </span>
          ))}
        </span>
      ) : (
        <span className={MUTED} data-summary="none">—</span>
      )}
      {cap && <div className="text-[var(--2a-navy)]" data-testid="run-cap-note">{cap}</div>}
      {waiting && <div className={MUTED} data-testid="run-waiting">{waiting}</div>}
    </div>
  );
}
