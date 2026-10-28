"use client";

/**
 * OneOffForm — ADVISE (REP7B): "Propose a one-off trade" on an account whose
 * authority is advise. A draft manual proposal (no model) the adviser then
 * submits; a second person approves; the member or another adviser executes
 * it with rep2's letter of instruction. Renders only when the server listed
 * `propose_one_off` and the caller may write (lib/authority.mjs); the
 * executor choices are the server's vocabulary.
 */

import { useState } from "react";
import { oneOffAccountControl, oneOffExecutors, vocab } from "@/lib/authority.mjs";

const card = "rounded-[6px] border border-[#ece8dd] bg-white";
const input = "rounded border border-[#E2E8F0] px-3 py-2";
const primary = "rounded bg-navy px-3 py-1 text-white disabled:opacity-50";

export default function OneOffForm({ envelope, holdings }) {
  const [form, setForm] = useState({ security: "", side: "sell", quantity: "", executor_type: "", executor_name: "",
    executor_contact: "" });
  const [msg, setMsg] = useState(null);
  if (!oneOffAccountControl(envelope)) return null;
  const execs = oneOffExecutors(envelope);
  const sides = Array.isArray(vocab(envelope).one_off_sides) ? vocab(envelope).one_off_sides : [];
  const rows = (Array.isArray(holdings) ? holdings : []).filter((h) => h && h.security_id);
  const set = (k, v) => setForm({ ...form, [k]: v });
  const a = envelope?.account || {};

  async function send() {
    setMsg(null);
    const body = {
      lines: [{ global_security_id: form.security, side: form.side, quantity: form.quantity.trim() || undefined }],
      executor_type: form.executor_type, executor_name: form.executor_name.trim() || undefined,
      executor_contact: form.executor_contact.trim() || undefined,
    };
    const res = await fetch(`/api/accounts/${a.id}/proposals/one-off`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) { setMsg(data.error || `Request failed (${res.status})`); return; }
    const pid = data?.proposal?.id;
    setMsg(pid ? "Draft one-off proposal made — open it to submit for approval." : "Draft made.");
    if (pid) window.location.href = `/portfolio/proposals/${pid}`;
  }

  return (
    <div className={`${card} p-6 text-sm`}>
      <h2 className="text-lg font-semibold text-navy">Propose a one-off trade</h2>
      <p className="mt-1 text-text-muted">Advise: the member or another adviser executes it, with a letter of instruction.</p>
      <div className="mt-3 flex flex-wrap items-end gap-2">
        <select className={input} value={form.security} onChange={(e) => set("security", e.target.value)}>
          <option value="">Choose a holding</option>
          {rows.map((h) => <option key={h.id} value={h.security_id}>{h.label}</option>)}
        </select>
        <select className={input} value={form.side} onChange={(e) => set("side", e.target.value)}>
          {sides.map((x) => <option key={x.key} value={x.key}>{x.label}</option>)}
        </select>
        <input className={input} placeholder={form.side === "sell" ? "Quantity (blank = all)" : "Quantity"} value={form.quantity} onChange={(e) => set("quantity", e.target.value)} />
        <select className={input} value={form.executor_type} onChange={(e) => set("executor_type", e.target.value)}>
          <option value="">Who executes</option>
          {execs.map((x) => <option key={x.key} value={x.key}>{x.label}</option>)}
        </select>
        <input className={input} placeholder="Name" value={form.executor_name} onChange={(e) => set("executor_name", e.target.value)} />
        <input className={input} placeholder="Contact" value={form.executor_contact} onChange={(e) => set("executor_contact", e.target.value)} />
        <button type="button" className={primary} disabled={!form.security || !form.executor_type || (form.side === "buy" && !form.quantity.trim())} onClick={send}>Make a draft</button>
      </div>
      {msg ? <p className="mt-2 text-navy">{msg}</p> : null}
    </div>
  );
}
