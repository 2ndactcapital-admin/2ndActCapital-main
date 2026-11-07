"use client";

/**
 * HeldAwayPanel — the account page's held-away section (REP7): Sources (kind,
 * status, last as-of, stale badge), Imports (the log, draft review with every
 * rejected row and its reason, approve / reject by a second person), and the
 * positions and lots with their source and as-of. Read-only data: there is NO
 * trade control here at all — a held-away account is never traded. Every
 * write control renders only from the envelope (lib/heldAway.mjs).
 */

import OptionContractPicker from "@/components/heldaway/OptionContractPicker";
import { contractTerms } from "@/lib/bugfix3.mjs";
import SecuritySearch from "@/components/heldaway/SecuritySearch";
import { useState } from "react";
import {
  blankLine, changeKind, draftSaveState, importStateText, lineFields, lineLabel, lineRow, neverMaster, qty,
} from "@/lib/bugfix4.mjs";
import {
  confirmControl, importControls, labelOf, manualRow, manualTermFields, money, panelControls, staleText, vocab,
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
          {importStateText(r, v) ? <p className="mt-1 text-xs text-navy">{importStateText(r, v)}</p> : null}
          {r.status === "draft" ? <p className="mt-1 text-xs text-text-muted">A draft changes nothing until a second person approves it.</p> : null}
          {r.status === "draft" && r.replaces ? <p className="mt-1 text-xs text-[#9B2335]">{r.replaces}</p> : null}
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
                  <li key={l.id}>Lot {l.acquired_date || "date unknown"}: {qty(l.quantity)} at cost {money(l.cost)} ({l.term || "term unknown"}{l.status === "closed" ? `, closed, realized ${money(l.realized_gain_loss)}` : ""})</li>
                ))}</ul> : null}
              </td>
              <td>{qty(p.quantity)}</td><td>{money(p.market_value)}</td><td>{p.as_of}</td>
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
  const [row, setRow] = useState(blankLine());
  // BUGFIX4 (O-04-02) — ONE draft per account: its lines, added / edited / removed before saving
  const [lines, setLines] = useState([]);
  const [editing, setEditing] = useState(null);
  const [replaces, setReplaces] = useState(null);
  const [ack, setAck] = useState(false);
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
  async function checkReplaces(date) {
    setReplaces(null);
    setAck(false);
    if (!date) return;
    const res = await fetch(`/api/held-away/accounts/${id}/snapshot?as_of=${encodeURIComponent(date)}`);
    const data = await res.json().catch(() => ({}));
    if (res.ok) setReplaces(data.replaces || null);
  }
  function keepLine() {
    if (editing === null) setLines([...lines, row]);
    else setLines(lines.map((l, i) => (i === editing ? row : l)));
    setRow(blankLine());          // the next line starts EMPTY — nothing carries over
    setEditing(null);
  }
  async function saveDraft() {
    const rows = lines.map((l) => lineRow(l, v, manualTermFields, manualRow));
    setMsg(null);
    try {
      await call(`accounts/${id}/imports/manual`, { as_of: asOf, rows, document_id: docId || undefined });
      setLines([]);
      setRow(blankLine());
      setAck(false);
      await reload();
      await checkReplaces(asOf);
    } catch (e) { setMsg(e.message); }
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
          <input className={input} type="date" value={asOf} onChange={(e) => { setAsOf(e.target.value); checkReplaces(e.target.value); }} aria-label="As of (statement date)" />
          {c.manualImport ? (
            <div className="space-y-2">
              <p className="text-xs text-text-muted">One draft holds every holding of the account as of this date. Add each line, then save the draft once.</p>
              {replaces ? (
                <label className="flex items-start gap-2 rounded border border-[#ece8dd] bg-[#FAF9F6] p-2 text-xs text-[#9B2335]">
                  <input type="checkbox" checked={ack} onChange={(e) => setAck(e.target.checked)} />
                  <span>{replaces}</span>
                </label>
              ) : null}
              {lines.length ? (
                <ul className="text-sm">{lines.map((l, i) => (
                  <li key={i} className="flex items-center gap-2 border-t border-[#ece8dd] py-1">
                    <span className="flex-1">{labelOf(v.manual_kinds, l.asset_kind) || "Listed security"} — {lineLabel(l)}</span>
                    <button type="button" className={secondary} onClick={() => { setRow(l); setEditing(i); }}>Edit</button>
                    <button type="button" className={secondary} onClick={() => setLines(lines.filter((_, j) => j !== i))}>Remove</button>
                  </li>
                ))}</ul>
              ) : null}
              <div className="flex flex-wrap gap-2">
                {/* PORTENTRY1 — a holding not in the security master: its type and terms (an org-scoped manual asset).
                    BUGFIX4 — changing the type starts the line over */}
                <select className={input} value={row.asset_kind || ""} onChange={(e) => setRow(changeKind(row, e.target.value))} aria-label="Holding type">
                  <option value="">Listed security (in the master)</option>
                  {(Array.isArray(v.manual_kinds) ? v.manual_kinds : []).map((k) => <option key={k.key} value={k.key}>{k.label}</option>)}
                </select>
                {/* PORTENTRY2 (P.1) — a listed security found in the master fills its ticker / CUSIP; BUGFIX4 — and only those */}
                {!row.asset_kind ? <SecuritySearch onPick={(sec) => setRow({ ...blankLine(), quantity: row.quantity, market_value: row.market_value,
                  ticker: sec.identifiers?.ticker || "", cusip: sec.identifiers?.cusip || "" })} /> : null}
                {lineFields(row.asset_kind, v, manualTermFields).map((k) => (
                  <input key={k} className={input}
                    placeholder={k === "market_value" && row.asset_kind && !lineFields(row.asset_kind, v, manualTermFields).includes("quantity") ? "Amount"
                      : (k === "ticker" && neverMaster(row.asset_kind, v) ? "Symbol (not matched to the security master)" : (labelOf(v.file_fields, k) || k.replace(/_/g, " ")))}
                    type={k === "maturity" || k === "expiry" || k === "acquired_date" ? "date" : "text"}
                    value={row[k] || ""} onChange={(e) => setRow({ ...row, [k]: e.target.value })} />
                ))}
                {row.asset_kind === "listed_option" ? (
                  <OptionContractPicker underlying={row.underlying_ticker}
                    onPick={(c) => setRow({ ...row, ...contractTerms(c) })} />
                ) : null}
                <button type="button" className={secondary} onClick={keepLine}>{editing === null ? "Add line" : "Update line"}</button>
                {editing !== null ? <button type="button" className={secondary} onClick={() => { setRow(blankLine()); setEditing(null); }}>Cancel edit</button> : null}
              </div>
              {(() => {
                const st = draftSaveState({ lines, asOf, replaces, acknowledged: ack, vocab: v });
                return (
                  <div className="flex items-center gap-2">
                    <button type="button" className={primary} disabled={!st.enabled} onClick={saveDraft}>Save as draft</button>
                    <span className="text-xs text-text-muted">{lines.length} line{lines.length === 1 ? "" : "s"} in this draft</span>
                    {st.reason ? <span className="text-xs text-text-muted">{st.reason}</span> : null}
                  </div>
                );
              })()}
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
