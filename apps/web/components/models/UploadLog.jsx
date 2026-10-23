"use client";

/**
 * UploadLog — Portfolio → Models → Upload log (REP3).
 *
 * Every model upload the org has made — draft created, rejected, abandoned,
 * duplicate, failed or still in progress — with its row counts and
 * row-numbered messages. The log is permanent; nothing here edits it. The CSV
 * export (every row, spreadsheet-formula prefixes neutralised by the server)
 * renders only when the envelope says `can_read === true`. Outcomes and
 * sources are the envelope's vocabularies.
 */

import { useState } from "react";
import { labelOf, uploadLogControls, vocab } from "@/lib/models.mjs";

const card = "rounded-[6px] border border-[#ece8dd] bg-white";
const input = "rounded border border-[#E2E8F0] px-3 py-2";

export function UploadTable({ envelope }) {
  const v = vocab(envelope);
  const rows = Array.isArray(envelope?.rows) ? envelope.rows : [];
  if (!rows.length) return <div className={`${card} p-10 text-center text-sm text-text-muted`}>No uploads match.</div>;
  return (
    <div className={`${card} overflow-x-auto`}>
      <table className="w-full text-sm">
        <thead className="border-b border-[#ece8dd] text-left text-text-muted">
          <tr>
            <th className="px-4 py-3 font-medium">Uploaded</th><th className="px-4 py-3 font-medium">File</th>
            <th className="px-4 py-3 font-medium">By</th><th className="px-4 py-3 font-medium">Provider</th>
            <th className="px-4 py-3 font-medium">Outcome</th><th className="px-4 py-3 font-medium">Model</th>
            <th className="px-4 py-3 font-medium">Rows</th><th className="px-4 py-3 font-medium">Messages</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id} className="border-b border-[#ece8dd] align-top last:border-0">
              <td className="px-4 py-3">{String(r.uploaded_at || "").slice(0, 16).replace("T", " ")}</td>
              <td className="px-4 py-3">{r.file_name}<div className="text-xs text-text-muted">{labelOf(v.upload_sources, r.source)}</div></td>
              <td className="px-4 py-3">{r.uploader_name || "—"}</td>
              <td className="px-4 py-3">{r.provider_name || "—"}</td>
              <td className="px-4 py-3">{labelOf(v.upload_outcomes, r.outcome)}{r.reason ? <div className="text-xs text-text-muted">{r.reason}</div> : null}</td>
              <td className="px-4 py-3">{r.model_id ? <a className="text-navy underline" href={`/portfolio/models/${r.model_id}`}>{r.model_name || "Model"}{r.version_no ? ` v${r.version_no}` : ""}</a> : "—"}</td>
              <td className="px-4 py-3">{r.rows_read != null ? `${r.rows_read} read · ${r.rows_resolved} resolved · ${r.rows_unresolved} unresolved · ${r.rows_cash} cash` : "—"}</td>
              <td className="px-4 py-3 text-xs">{(r.messages || []).slice(0, 4).map((m, i) => (
                <div key={i}>{m.row ? `Row ${m.row}: ` : ""}{m.text}</div>
              ))}{(r.messages || []).length > 4 ? <div className="text-text-muted">+{r.messages.length - 4} more</div> : null}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function UploadLog({ initialEnvelope }) {
  const [env, setEnv] = useState(initialEnvelope);
  const [filters, setFilters] = useState({});
  const v = vocab(env);
  const c = uploadLogControls(env);
  async function reload(next) {
    setFilters(next);
    const params = new URLSearchParams();
    for (const [k, val] of Object.entries(next)) if (val) params.set(k, String(val));
    const res = await fetch(`/api/models/uploads?${params}`);
    const data = await res.json().catch(() => ({}));
    if (res.ok) setEnv(data);
  }
  const set = (k) => (e) => reload({ ...filters, [k]: e.target.value });
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3 text-sm">
        <div className="flex flex-wrap gap-3">
          <label className="flex flex-col gap-1">Outcome
            <select className={input} value={filters.outcome || ""} onChange={set("outcome")}>
              <option value="">Any</option>
              {(v.upload_outcomes || []).map((o) => <option key={o.key} value={o.key}>{o.label}</option>)}
            </select>
          </label>
          <label className="flex flex-col gap-1">Provider
            <input className={input} value={filters.provider || ""} onChange={set("provider")} />
          </label>
          <label className="flex flex-col gap-1">From<input type="date" className={input} value={filters.since || ""} onChange={set("since")} /></label>
          <label className="flex flex-col gap-1">To<input type="date" className={input} value={filters.until || ""} onChange={set("until")} /></label>
        </div>
        {c.exportCsv ? <a className="rounded border border-navy px-4 py-2 text-navy" href="/api/models/uploads/export.csv">Export CSV</a> : null}
      </div>
      <UploadTable envelope={env} />
    </div>
  );
}
