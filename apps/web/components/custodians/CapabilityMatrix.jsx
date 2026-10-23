"use client";

/**
 * CapabilityMatrix — Billing & Custody → Custodian capabilities (REP2).
 *
 * Custodians × capabilities from GET /custodians/capabilities, each cell with
 * its value, evidence and as-of date. "Unknown" is shown plainly — and is
 * treated as not available everywhere. Edit renders only from
 * `capabilityControls(envelope)`: the server's can_write AND is_super_admin.
 * Every edit is a new version (the old one is kept); labels and allowed
 * values are the envelope's vocabularies.
 */

import { useState } from "react";
import { capabilityCell, capabilityControls, labelOf, vocab } from "@/lib/execution.mjs";

const card = "rounded-[6px] border border-[#ece8dd] bg-white";
const input = "rounded border border-[#E2E8F0] px-2 py-1";

export function EditCell({ envelope, custodian, cell, onSaved }) {
  const v = vocab(envelope);
  const cap = (v.capabilities || []).find((c) => c.key === cell.capability_key);
  const [f, setF] = useState({ value: cell.value, evidence_kind: cell.evidence_kind || "assumed", evidence: "",
    as_of: new Date().toISOString().slice(0, 10), note: "" });
  const [msg, setMsg] = useState(null);
  async function save(e) {
    e.preventDefault();
    setMsg(null);
    const res = await fetch(`/api/custodians/capabilities/${custodian}/${cell.capability_key}`, {
      method: "PUT", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ...f, note: f.note || undefined }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) { setMsg(data.error || `Save failed (${res.status})`); return; }
    onSaved?.();
  }
  return (
    <form onSubmit={save} className="mt-2 flex flex-col gap-1 text-xs">
      <select className={input} value={f.value} onChange={(e) => setF({ ...f, value: e.target.value })}>
        {(cap?.values || []).map((val) => <option key={val} value={val}>{labelOf(v.values, val)}</option>)}
      </select>
      <select className={input} value={f.evidence_kind} onChange={(e) => setF({ ...f, evidence_kind: e.target.value })}>
        {(v.evidence_kinds || []).map((k) => <option key={k.key} value={k.key}>{k.label}</option>)}
      </select>
      <input className={input} placeholder="Evidence (document, URL or why assumed)" value={f.evidence}
        onChange={(e) => setF({ ...f, evidence: e.target.value })} />
      <input type="date" className={input} value={f.as_of} onChange={(e) => setF({ ...f, as_of: e.target.value })} />
      <button type="submit" className="rounded bg-navy px-2 py-1 text-white">Save new version</button>
      {msg ? <span className="text-[#9B2335]">{msg}</span> : null}
    </form>
  );
}

export function MatrixTable({ envelope, onSaved }) {
  const v = vocab(envelope);
  const rows = Array.isArray(envelope?.rows) ? envelope.rows : [];
  const controls = capabilityControls(envelope);
  const [editing, setEditing] = useState(null);
  const caps = Array.isArray(v.capabilities) ? v.capabilities : [];
  if (!rows.length) {
    return <div className={`${card} p-10 text-center text-sm text-text-muted`}>No custodians recorded.</div>;
  }
  return (
    <div className={`${card} overflow-x-auto`}>
      <table className="w-full text-sm">
        <thead className="border-b border-[#ece8dd] text-left text-text-muted">
          <tr>
            <th className="px-4 py-3 font-medium">Capability</th>
            {rows.map((r) => <th key={r.custodian_code} className="px-4 py-3 font-medium">{r.label}</th>)}
          </tr>
        </thead>
        <tbody>
          {caps.map((cap) => (
            <tr key={cap.key} className="border-b border-[#ece8dd] align-top last:border-0">
              <td className="px-4 py-3 font-medium text-navy">{cap.label}</td>
              {rows.map((r) => {
                const cell = (r.cells || []).find((c) => c.capability_key === cap.key);
                const shown = capabilityCell(cell, v.values);
                const key = `${r.custodian_code}:${cap.key}`;
                return (
                  <td key={key} className="px-4 py-3">
                    <span className={shown.available ? "text-[#2D6A4F]" : "text-text-muted"}>{shown.text}</span>
                    {cell ? (
                      <span className="block text-xs text-text-muted">
                        {labelOf(v.evidence_kinds, cell.evidence_kind)}: {cell.evidence}
                        {cell.as_of ? ` · as of ${cell.as_of}` : ""}{cell.version_no ? ` · v${cell.version_no}` : ""}
                      </span>
                    ) : null}
                    {controls.edit ? (
                      editing === key ? (
                        <EditCell envelope={envelope} custodian={r.custodian_code} cell={cell}
                          onSaved={() => { setEditing(null); onSaved?.(); }} />
                      ) : (
                        <button type="button" className="mt-1 text-xs text-navy underline" onClick={() => setEditing(key)}>Edit</button>
                      )
                    ) : null}
                  </td>
                );
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function CapabilityMatrix({ initialEnvelope }) {
  const [env, setEnv] = useState(initialEnvelope);
  async function reload() {
    const res = await fetch("/api/custodians/capabilities");
    const data = await res.json().catch(() => ({}));
    if (res.ok) setEnv(data);
  }
  return (
    <div className="space-y-3">
      <p className="text-sm text-text-muted">{vocab(env).unknown_rule}</p>
      <MatrixTable envelope={env} onSaved={reload} />
    </div>
  );
}
