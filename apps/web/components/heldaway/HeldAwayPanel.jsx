"use client";

/**
 * HeldAwayPanel — the account page's held-away section (REP7): Sources (kind,
 * status, last as-of, stale badge), Imports (the log, draft review with every
 * rejected row and its reason, approve / reject by a second person), and the
 * positions and lots with their source and as-of. Read-only data: there is NO
 * trade control here at all — a held-away account is never traded. Every
 * write control renders only from the envelope (lib/heldAway.mjs).
 */

import { useState } from "react";
import {
  confirmControl, importControls, labelOf, manualRow, money, panelControls, staleText, vocab,
} from "@/lib/heldAway.mjs";

const card = "rounded-[6px] border border-[#ece8dd] bg-white";
const input = "rounded border border-[#E2E8F0] px-3 py-2";
const primary = "rounded bg-navy px-3 py-1 text-white disabled:opacity-50";
const secondary = "rounded border border-navy px-3 py-1 text-navy disabled:opacity-50";

async function call(path, body, { form } = {}) {
  const res = await fetch(`/api/held-away/${path}`, form ? { method: "POST", body: form } : {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
  return data;
}

export function Sources({ envelope }) {
  const v = vocab(envelope);
  const rows = Array.isArray(envelope?.panel?.sources) ? envelope.panel.sources : [];
  if (!rows.length) return <p className="text-text-muted">No source yet — the first import adds one.</p>;
  return (
    <table className="w-full">
      <thead className="text-left text-text-muted"><tr><th className="py-1">Source</th><th>Status</th><th>Last as-of</th><th /></tr></thead>
      <tbody>{rows.map((s) => (
        <tr key={s.id} className="border-t border-[#ece8dd]">
          <td className="py-1">{labelOf(v.source_kinds, s.kind)}{s.provider ? ` — ${labelOf(v.providers, s.provider)}` : ""}</td>
          <td>{labelOf(v.source_statuses, s.status)}</td>
          <td>{s.last_as_of || "—"}</td>
          <td>{staleText(s) ? <span className="rounded bg-[#FAF9F6] px-2 text-xs text-[#9B2335]">{staleText(s)}</span> : null}</td>
        </tr>
      ))}</tbody>
    </table>
  );
}

export function Imports({ envelope, onAct }) {
  const v = vocab(envelope);
  const rows = Array.isArray(envelope?.imports) ? envelope.imports : [];
  const [note, setNote] = useState("");
  if (!rows.length) return <p className="text-text-muted">No import yet.</p>;
  return (
    <ul className="space-y-3">{rows.map((r) => {
      const c = importControls(envelope, r);
      const rej = Array.isArray(r.rejects) ? r.rejects : [];
      return (
        <li key={r.id} className="border-t border-[#ece8dd] pt-2">
          <p><span className="font-medium">{labelOf(v.source_kinds, r.kind)}</span> as of {r.as_of} · {labelOf(v.import_statuses, r.status)}
            {" "}· read {r.rows_read}, accepted {r.rows_accepted}, rejected {r.rows_rejected}{r.file_name ? ` · ${r.file_name}` : ""}</p>
          {r.extraction?.label ? <p className="text-xs text-text-muted">{r.extraction.label} ({(r.extraction.models || []).join(", ")})</p> : null}
          {rej.length ? <ul className="mt-1 text-xs text-[#9B2335]">{rej.map((x, i) => (
            <li key={i}>Row {x.row ?? "—"}: {labelOf(v.reject_reasons, x.code)} — {x.detail}</li>
          ))}</ul> : null}
          {r.status === "draft" ? <p className="mt-1 text-xs text-text-muted">A draft changes nothing until a second person approves it.</p> : null}
          {c.approve || c.reject ? (
            <div className="mt-2 flex flex-wrap items-center gap-2">
              <input className={input} placeholder="Note (required to reject)" value={note} onChange={(e) => setNote(e.target.value)} />
              {c.approve ? <button type="button" className={primary} onClick={() => onAct?.(`imports/${r.id}/approve`, { note: note || undefined })}>Approve</button> : null}
              {c.reject ? <button type="button" className={secondary} onClick={() => onAct?.(`imports/${r.id}/reject`, { note })}>Reject</button> : null}
            </div>
          ) : null}
        </li>
      );
    })}</ul>
  );
}

export function Positions({ envelope }) {
  const pos = Array.isArray(envelope?.positions?.positions) ? envelope.positions.positions : [];
  const v = vocab(envelope);
  const versions = Array.isArray(envelope?.positions?.versions) ? envelope.positions.versions : [];
  return (
    <div>
      {pos.length ? (
        <table className="w-full">
          <thead className="text-left text-text-muted"><tr><th className="py-1">Holding</th><th>Quantity</th><th>Market value</th><th>As of</th><th>Source</th></tr></thead>
          <tbody>{pos.map((p) => (
            <tr key={p.id} className="border-t border-[#ece8dd] align-top">
              <td className="py-1">{p.label}
                {Array.isArray(p.lots) && p.lots.length ? <ul className="text-xs text-text-muted">{p.lots.map((l) => (
                  <li key={l.id}>Lot {l.acquired_date || "date unknown"}: {l.quantity} at cost {money(l.cost)} ({l.term || "term unknown"}{l.status === "closed" ? `, closed, realized ${money(l.realized_gain_loss)}` : ""})</li>
                ))}</ul> : null}
              </td>
              <td>{p.quantity}</td><td>{money(p.market_value)}</td><td>{p.as_of}</td>
              <td>{labelOf(v.source_kinds, p.source)}</td>
            </tr>
          ))}</tbody>
        </table>
      ) : <p className="text-text-muted">No positions on record.</p>}
      {versions.length > 1 ? <p className="mt-2 text-xs text-text-muted">Earlier versions kept: {versions.filter((x) => !x.current).map((x) => x.as_of).join(", ")}</p> : null}
    </div>
  );
}

export default function HeldAwayPanel({ initialEnvelope }) {
  const [env, setEnv] = useState(initialEnvelope);
  const [msg, setMsg] = useState(null);
  const [asOf, setAsOf] = useState("");
  const [row, setRow] = useState({});
  const [file, setFile] = useState(null);
  const [docId, setDocId] = useState("");
  const panel = env?.panel;
  if (!panel || typeof panel !== "object" || panel.account?.is_held_away !== true) return null;
  const v = vocab(env);
  const c = panelControls(env);
  const st = panel.statement_extraction || {};
  const confs = Array.isArray(panel.confirmations) ? panel.confirmations : [];
  const id = panel.account.id;

  async function reload() {
    const res = await fetch(`/api/held-away/accounts/${id}`);
    const data = await res.json().catch(() => ({}));
    if (res.ok) setEnv(data);
  }
  async function act(path, body, opts) {
    setMsg(null);
    try { await call(path, body, opts); await reload(); } catch (e) { setMsg(e.message); }
  }
  async function upload() {
    if (!file || !asOf) { setMsg("Choose a file and its as-of date."); return; }
    const fd = new FormData();
    fd.append("file", file);
    fd.append("as_of", asOf);
    await act(`accounts/${id}/imports/file`, null, { form: fd });
  }

  return (
    <div className={`${card} p-6 text-sm`}>
      <h2 className="text-lg font-semibold text-navy">Held-away account</h2>
      <p className="mt-1 text-text-muted">{panel.trade_note}</p>
      <p className="mt-1">Data as of {panel.as_of || "never"} · stale after {panel.stale_after_days} day(s) ({panel.threshold_from})
        {panel.stale ? <span className="ml-2 rounded bg-[#FAF9F6] px-2 text-xs text-[#9B2335]">Stale</span> : null}</p>
      <h3 className="mt-4 font-medium text-navy">Sources</h3>
      <Sources envelope={env} />
      <p className="mt-1 text-xs text-text-muted">Aggregators: {(Array.isArray(panel.providers) ? panel.providers : []).map((p) => `${p.label} (${labelOf(v.source_statuses, p.status)})`).join(" · ")}</p>
      <h3 className="mt-4 font-medium text-navy">Positions and lots</h3>
      <Positions envelope={env} />
      <h3 className="mt-4 font-medium text-navy">Imports</h3>
      <Imports envelope={env} onAct={act} />
      <h3 className="mt-4 font-medium text-navy">Statements</h3>
      {!st.available ? <p className="text-text-muted">{st.message}</p> : null}
      {c.linkStatement ? (
        <div className="mt-2 flex flex-wrap gap-2">
          <input className={input} placeholder="Chancery document id" value={docId} onChange={(e) => setDocId(e.target.value)} />
          <button type="button" className={secondary} onClick={() => act(`accounts/${id}/statements`, { document_id: docId })}>Link statement</button>
          {c.statementImport ? <button type="button" className={secondary} onClick={() => act(`accounts/${id}/imports/statement`, { document_id: docId, as_of: asOf })}>Extract statement</button> : null}
        </div>
      ) : null}
      {c.manualImport || c.fileImport ? (
        <div className="mt-4 space-y-2">
          <h3 className="font-medium text-navy">Enter positions</h3>
          <input className={input} type="date" value={asOf} onChange={(e) => setAsOf(e.target.value)} aria-label="As of (statement date)" />
          {c.manualImport ? (
            <div className="flex flex-wrap gap-2">
              {["ticker", "cusip", "quantity", "market_value", "acquired_date", "cost"].map((k) => (
                <input key={k} className={input} placeholder={labelOf(v.file_fields, k)} value={row[k] || ""} onChange={(e) => setRow({ ...row, [k]: e.target.value })} />
              ))}
              <button type="button" className={secondary} onClick={() => act(`accounts/${id}/imports/manual`, { as_of: asOf, rows: [manualRow(row)], document_id: docId || undefined })}>Save as draft</button>
            </div>
          ) : null}
          {c.fileImport ? (
            <div className="flex flex-wrap items-center gap-2">
              <input type="file" accept=".xlsx,.xls,.csv" onChange={(e) => setFile(e.target.files?.[0] || null)} />
              <button type="button" className={secondary} onClick={upload}>Upload file</button>
            </div>
          ) : null}
        </div>
      ) : null}
      <h3 className="mt-4 font-medium text-navy">Stale-data confirmations</h3>
      {confs.length ? <ul>{confs.map((x) => (
        <li key={x.id}>{labelOf(v.confirmation_statuses, x.status)} · data as of {x.as_of_confirmed}{x.expires_on ? ` · expires ${x.expires_on}` : ""}{x.closed_reason ? ` · ${x.closed_reason}` : ""}
          {confirmControl(env, x) ? <button type="button" className={`${secondary} ml-2`} onClick={() => act(`confirmations/${x.id}/confirm`, {})}>Confirm the data</button> : null}</li>
      ))}</ul> : <p className="text-text-muted">None.</p>}
      {c.requestConfirmation ? <button type="button" className={`${secondary} mt-2`} onClick={() => act(`accounts/${id}/confirmations`, {})}>Ask for a confirmation</button> : null}
      {msg ? <p className="mt-2 text-[#9B2335]">{msg}</p> : null}
    </div>
  );
}
