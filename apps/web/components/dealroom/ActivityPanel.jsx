"use client";

/**
 * ActivityPanel — the deal's activity for its managers (DEALROOM3).
 *
 * GET /deal-room/deals/{id}/activity?view=investor|document|events, with a
 * CSV export of the same view. The server refuses anyone but the deal's
 * managers; the component itself renders nothing unless the room envelope
 * says can_write === true. Records are kept after access ends and after the
 * deal closes.
 */

import { useState } from "react";
import { canWrite } from "@/lib/dealRoom.mjs";

const COLUMNS = {
  investor: [["user_name", "Reader"], ["on_behalf_of_name", "Advisor to"], ["entity_name", "Entity"],
    ["first_open", "First open"], ["last_open", "Last open"], ["documents_viewed", "Documents viewed"],
    ["documents_downloaded", "Downloaded"], ["pages_viewed", "Pages"], ["questions", "Questions"]],
  document: [["file_name", "Document"], ["readers", "Readers"], ["pages_viewed", "Pages viewed"],
    ["downloads", "Downloads"], ["first_at", "First"], ["last_at", "Last"]],
  events: [["at", "When"], ["action", "Action"], ["user_name", "Reader"], ["on_behalf_of_name", "Advisor to"],
    ["entity_name", "Entity"], ["file_name", "Document"], ["page", "Page"]],
};

function cell(v) {
  if (v == null) return "—";
  return typeof v === "string" && /^\d{4}-\d{2}-\d{2}T/.test(v) ? v.slice(0, 16).replace("T", " ") : String(v);
}

export default function ActivityPanel({ envelope, initial }) {
  const [view, setView] = useState(initial?.view || "investor");
  const [data, setData] = useState(initial || null);
  const [error, setError] = useState(null);
  if (!canWrite(envelope)) return null;
  const dealId = envelope.deal.id;
  const load = async (v) => {
    setView(v);
    const res = await fetch(`/api/deal-room/deals/${dealId}/activity?view=${v}`);
    const body = await res.json().catch(() => ({}));
    if (!res.ok) { setError(body.error || `Request failed (${res.status})`); return; }
    setError(null);
    setData(body);
  };
  const cols = COLUMNS[view] || [];
  return (
    <section className="rounded-[6px] border border-[#ece8dd] bg-white p-5" data-testid="activity-panel">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-base font-semibold text-navy">Activity</h3>
        <div className="flex gap-2 text-xs">
          {Object.keys(COLUMNS).map((v) => (
            <button key={v} type="button" className={`rounded border px-2 py-1 ${v === view ? "border-navy text-navy" : "border-border text-text-muted"}`}
              onClick={() => load(v)}>{v === "investor" ? "By investor" : v === "document" ? "By document" : "Every event"}</button>
          ))}
          <a className="rounded border border-navy px-2 py-1 text-navy" href={`/api/deal-room/deals/${dealId}/activity?view=${view}&format=csv`}>
            Export CSV
          </a>
        </div>
      </div>
      <p className="mt-1 text-xs text-text-muted">Kept after access ends and after the deal closes.</p>
      {!data ? (
        <button type="button" className="mt-3 text-sm text-navy hover:underline" onClick={() => load(view)}>Load activity</button>
      ) : (
        <div className="mt-3 overflow-x-auto">
          <table className="min-w-full text-left text-xs">
            <thead><tr>{cols.map(([k, h]) => <th key={k} className="border-b border-border px-2 py-1 font-medium text-text-muted">{h}</th>)}</tr></thead>
            <tbody>
              {(data.rows || []).map((r, i) => (
                <tr key={i} className="border-b border-[#ece8dd]">
                  {cols.map(([k]) => <td key={k} className="px-2 py-1 text-text-secondary">{cell(r[k])}</td>)}
                </tr>
              ))}
            </tbody>
          </table>
          {(data.rows || []).length === 0 && <p className="mt-2 text-sm text-text-muted">No activity yet.</p>}
        </div>
      )}
      {error && <p className="mt-2 text-sm text-[#9B2335]" role="alert">{error}</p>}
    </section>
  );
}
