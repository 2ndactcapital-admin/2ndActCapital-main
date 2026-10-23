"use client";

/**
 * UploadWizard — Portfolio → Models → Upload model (REP3).
 *
 * file → sheet → header row → column mapping (the server's suggestions, a
 * provider's saved mapping applied first, Save mapping) → validation report
 * (row-numbered messages, unresolved rows highlighted, tradability notes) →
 * create draft. Every step is the server's: the file is parsed, validated and
 * resolved by POST /models/uploads/{id}/preview; nothing here interprets a
 * spreadsheet. The whole wizard renders only when the envelope says
 * `can_write === true`. Field names, severities and codes are the envelope's
 * vocabularies.
 */

import { useState } from "react";
import { labelOf, messagesByRow, pctText, perms, positionLabel, vocab } from "@/lib/models.mjs";

const card = "rounded-[6px] border border-[#ece8dd] bg-white";
const input = "rounded border border-[#E2E8F0] px-3 py-2";
const primary = "rounded bg-navy px-4 py-2 text-white disabled:opacity-50";
const secondary = "rounded border border-navy px-4 py-2 text-navy disabled:opacity-50";
const SEVERITY_CLASS = { error: "text-[#9B2335]", warning: "text-[#8a5a00]", info: "text-text-muted" };

async function post(path, body) {
  const res = await fetch(`/api/models/${path}`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = new Error(data.error || `Request failed (${res.status})`);
    err.detail = data.detail;
    throw err;
  }
  return data;
}

export function MessageList({ messages, envelope }) {
  const v = vocab(envelope);
  const groups = messagesByRow(messages);
  if (!groups.size) return null;
  return (
    <ul className="space-y-1 text-sm">
      {[...groups.entries()].map(([row, list]) => list.map((m, i) => (
        <li key={`${row}-${i}`} className={SEVERITY_CLASS[m.severity] || ""}>
          <span className="font-medium">{row ? `Row ${row}` : "File"}</span>
          {" — "}{labelOf(v.validation_codes, m.code)}: {m.text}
        </li>
      )))}
    </ul>
  );
}

export function WizardReport({ envelope, report, tradability, diff }) {
  if (!report) return null;
  const v = vocab(envelope);
  const c = report.counts || {};
  const positions = Array.isArray(report.positions) ? report.positions : [];
  return (
    <div className="space-y-4">
      <div className={`${card} p-5 text-sm`}>
        <p className="font-medium text-navy">Validation report</p>
        <p className="mt-1 text-text-muted">
          Sheet {report.sheet}, header row {report.header_row}; weights read as{" "}
          {labelOf(v.weight_bases, report.weight_basis).toLowerCase() || "—"}. {c.read ?? 0} rows: {c.resolved ?? 0} resolved,{" "}
          {c.unresolved ?? 0} unresolved (queued, never guessed), {c.cash ?? 0} cash.
        </p>
        <div className="mt-3"><MessageList messages={report.messages} envelope={envelope} /></div>
        {Array.isArray(tradability) && tradability.length ? (
          <div className="mt-3">
            <p className="text-text-muted">Tradability (informational, never blocking)</p>
            <ul className="text-text-muted">{tradability.map((t, i) => <li key={i}>{t.text}</li>)}</ul>
          </div>
        ) : null}
      </div>
      {diff && diff.has_base ? (
        <div className={`${card} p-5 text-sm`}>
          <p className="font-medium text-navy">Against the approved version</p>
          <p className="mt-1">{diff.added.length} added, {diff.removed.length} removed, {diff.changed.length} weight
            changes; turnover {pctText(diff.turnover)}</p>
        </div>
      ) : null}
      <div className={`${card} overflow-x-auto`}>
        <table className="w-full text-sm">
          <thead className="border-b border-[#ece8dd] text-left text-text-muted">
            <tr><th className="px-4 py-2 font-medium">Row</th><th className="px-4 py-2 font-medium">Position</th>
              <th className="px-4 py-2 font-medium">Weight</th><th className="px-4 py-2 font-medium">Resolution</th></tr>
          </thead>
          <tbody>
            {positions.map((p) => (
              <tr key={p.row_num} className={`border-b border-[#ece8dd] last:border-0 ${p.resolution_state === "unresolved" ? "bg-[#fdf6ec]" : ""}`}>
                <td className="px-4 py-2">{p.row_num}</td>
                <td className="px-4 py-2">{positionLabel(p)}{p.raw_cusip ? <span className="ml-2 text-xs text-text-muted">{p.raw_cusip}</span> : null}</td>
                <td className="px-4 py-2">{pctText(p.target_weight)}</td>
                <td className="px-4 py-2">{p.resolution_state === "unresolved" ? `Unresolved — ${p.resolution_note || "queued"}` : p.resolution_state === "cash" ? "Cash" : "Resolved"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

export function MappingTable({ envelope, headers, mapping, suggestions, onChange }) {
  const v = vocab(envelope);
  const src = new Map((suggestions || []).map((s) => [s.header, s.source]));
  return (
    <table className="w-full text-sm">
      <thead className="text-left text-text-muted"><tr><th className="py-1 font-medium">Column in the file</th>
        <th className="py-1 font-medium">Field</th><th className="py-1 font-medium" /></tr></thead>
      <tbody>
        {(headers || []).filter((h) => h).map((h) => (
          <tr key={h}>
            <td className="py-1 pr-3">{h}</td>
            <td className="py-1 pr-3">
              <select className={input} value={mapping[h] || ""} onChange={(e) => onChange({ ...mapping, [h]: e.target.value })}>
                <option value="">Ignore</option>
                {(v.mapping_fields || []).map((f) => <option key={f.key} value={f.key}>{f.label}</option>)}
              </select>
            </td>
            <td className="py-1 text-xs text-text-muted">{src.get(h) === "saved" ? "saved mapping" : src.get(h) === "synonym" ? "suggested" : ""}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export default function UploadWizard({ envelope, targetModel = null }) {
  const canWrite = perms(envelope).can_write === true;
  const [file, setFile] = useState(null);
  const [provider, setProvider] = useState("");
  const [step, setStep] = useState(null);
  const [mapping, setMapping] = useState({});
  const [sheet, setSheet] = useState("");
  const [headerRow, setHeaderRow] = useState("");
  const [fillCash, setFillCash] = useState(false);
  const [preview, setPreview] = useState(null);
  const [modelName, setModelName] = useState("");
  const [msg, setMsg] = useState(null);
  const [messages, setMessages] = useState([]);
  const [busy, setBusy] = useState(false);
  if (!canWrite) return null;

  const fromWizard = (w) => {
    setSheet(w.sheet || "");
    setHeaderRow(w.header_row ? String(w.header_row) : "");
    const m = {};
    for (const s of w.suggestions || []) if (s.header) m[s.header] = s.field || "";
    setMapping(m);
  };

  async function upload() {
    setMsg(null); setMessages([]); setBusy(true);
    const fd = new FormData();
    fd.append("file", file);
    if (provider.trim()) fd.append("provider_name", provider.trim());
    if (targetModel?.id) fd.append("model_id", targetModel.id);
    const res = await fetch("/api/models/uploads", { method: "POST", body: fd });
    const data = await res.json().catch(() => ({}));
    setBusy(false);
    if (!res.ok) {
      setMsg(data.error || `Upload refused (${res.status}) — it is recorded in the upload log`);
      setMessages(data.detail?.messages || []);
      return;
    }
    if (data.duplicate_of) {
      setMsg(`This exact file was uploaded before (${data.duplicate_of.file_name}, ${String(data.duplicate_of.uploaded_at || "").slice(0, 10)}) — logged as a duplicate.`);
      return;
    }
    setStep(data);
    fromWizard(data);
    if (!modelName) setModelName((file?.name || "").replace(/\.[^.]+$/, ""));
  }

  const stepBody = () => ({
    sheet: sheet || null, header_row: headerRow ? Number(headerRow) : null, mapping,
    provider_name: provider.trim() || null, fill_with_cash: fillCash, model_id: targetModel?.id || null,
  });

  async function runPreview(resetMapping = false) {
    setMsg(null); setMessages([]); setBusy(true);
    try {
      const body = stepBody();
      if (resetMapping) body.mapping = null;
      const out = await post(`uploads/${step.upload.id}/preview`, body);
      setPreview(out);
      if (resetMapping) fromWizard(out);
    } catch (e) { setMsg(e.message); setMessages(e.detail?.messages || []); }
    setBusy(false);
  }

  async function saveMapping() {
    setMsg(null);
    try {
      await post("mappings", { provider_name: provider.trim(), mapping, sheet_hint: sheet || null,
        header_row_hint: headerRow ? Number(headerRow) : null, upload_id: step.upload.id });
      setMsg(`Mapping saved for ${provider.trim()} — it is applied first on that provider's next upload.`);
    } catch (e) { setMsg(e.message); }
  }

  async function confirm() {
    setMsg(null); setMessages([]); setBusy(true);
    try {
      const out = await post(`uploads/${step.upload.id}/confirm`, { ...stepBody(), model_name: targetModel ? null : modelName });
      window.location.href = `/portfolio/models/${out.model.id}`;
    } catch (e) { setMsg(e.message); setMessages(e.detail?.messages || []); }
    setBusy(false);
  }

  async function abandonUpload() {
    const reason = window.prompt("Why is this upload being abandoned?");
    if (!reason) return;
    try {
      await post(`uploads/${step.upload.id}/abandon`, { reason });
      window.location.href = "/portfolio/models/uploads";
    } catch (e) { setMsg(e.message); }
  }

  return (
    <div className="space-y-5">
      <div className={`${card} space-y-3 p-5 text-sm`}>
        <p className="font-medium text-navy">1. File{targetModel ? ` — a new version of ${targetModel.name}` : ""}</p>
        <input type="file" accept=".xlsx,.xls,.csv" onChange={(e) => setFile(e.target.files?.[0] || null)} />
        <label className="flex flex-col gap-1">Provider (optional — its saved mapping is applied first)
          <input className={input} value={provider} onChange={(e) => setProvider(e.target.value)} />
        </label>
        <button type="button" className={primary} disabled={!file || busy || !!step} onClick={upload}>Upload</button>
        <p className="text-xs text-text-muted">.xlsx, .xls or .csv, up to 5 MB and 5,000 rows. Formulas are never evaluated;
          macro-enabled workbooks are refused. Every upload is kept in the upload log.</p>
      </div>
      {step ? (
        <div className={`${card} space-y-3 p-5 text-sm`}>
          <p className="font-medium text-navy">2. Sheet, header row and columns</p>
          <div className="flex flex-wrap gap-3">
            <label className="flex flex-col gap-1">Sheet
              <select className={input} value={sheet} onChange={(e) => setSheet(e.target.value)}>
                {(step.sheets || []).map((s) => <option key={s.name} value={s.name}>{s.name} ({s.rows} rows)</option>)}
              </select>
            </label>
            <label className="flex flex-col gap-1">Header row
              <input className={`${input} w-24`} value={headerRow} onChange={(e) => setHeaderRow(e.target.value)} />
            </label>
            <button type="button" className={secondary} onClick={() => runPreview(true)}>Re-read columns</button>
          </div>
          <MappingTable envelope={envelope} headers={(preview || step).headers} mapping={mapping}
            suggestions={(preview || step).suggestions} onChange={setMapping} />
          <label className="flex items-center gap-2">
            <input type="checkbox" checked={fillCash} onChange={(e) => setFillCash(e.target.checked)} />
            Fill any gap below 100% with cash
          </label>
          <div className="flex flex-wrap gap-2">
            <button type="button" className={primary} disabled={busy} onClick={() => runPreview(false)}>Validate</button>
            <button type="button" className={secondary} disabled={!provider.trim()} onClick={saveMapping}>Save mapping</button>
            <button type="button" className={secondary} onClick={abandonUpload}>Abandon upload</button>
          </div>
        </div>
      ) : null}
      {msg ? <p className="text-sm text-[#9B2335]">{msg}</p> : null}
      {messages.length ? <MessageList messages={messages} envelope={envelope} /> : null}
      {preview ? (
        <>
          <WizardReport envelope={envelope} report={preview.report} tradability={preview.tradability} diff={preview.diff} />
          <div className={`${card} space-y-3 p-5 text-sm`}>
            <p className="font-medium text-navy">3. Create the draft</p>
            {!targetModel ? (
              <label className="flex flex-col gap-1">Model name
                <input className={input} value={modelName} onChange={(e) => setModelName(e.target.value)} />
              </label>
            ) : null}
            <button type="button" className={primary} disabled={busy || preview.report?.blocking || (!targetModel && !modelName.trim())}
              onClick={confirm}>Create draft</button>
            {preview.report?.blocking ? <p className="text-[#9B2335]">Fix the errors above first.</p> : null}
          </div>
        </>
      ) : null}
    </div>
  );
}
