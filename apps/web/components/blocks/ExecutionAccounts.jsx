"use client";

/**
 * ExecutionAccounts — Settings → Executing accounts (REP9).
 *
 * The firm's own executing (omnibus) accounts at brokers — an org-level
 * record, never a member account. The reference is entered as its last four
 * characters and stored masked. Add / Retire render only with the envelope's
 * can_write === true (fail closed).
 */

import { useState } from "react";
import { executionAccountControls, labelOf, vocab } from "@/lib/blocks.mjs";

const card = "rounded-[6px] border border-[#ece8dd] bg-white";
const input = "rounded border border-[#E2E8F0] px-3 py-2";
const primary = "rounded bg-navy px-3 py-1 text-white disabled:opacity-50";
const secondary = "rounded border border-navy px-3 py-1 text-navy disabled:opacity-50";

async function send(path, body, method = "POST") {
  const res = await fetch(`/api/execution/${path}`, {
    method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
  return data;
}

export default function ExecutionAccounts({ initialEnvelope }) {
  const [env, setEnv] = useState(initialEnvelope);
  const rows = Array.isArray(env?.rows) ? env.rows : [];
  const v = vocab(env);
  const c = executionAccountControls(env);
  const [f, setF] = useState({ broker: "", last4: "", dvp: true, contact: "" });
  const [msg, setMsg] = useState(null);
  async function reload() {
    const res = await fetch("/api/execution/blocks/settings/execution-accounts");
    const data = await res.json().catch(() => ({}));
    if (res.ok) setEnv(data);
  }
  async function add() {
    setMsg(null);
    try {
      await send("blocks/settings/execution-accounts", { broker_name: f.broker, reference_last4: f.last4,
        dvp_capable: f.dvp, contact: f.contact || undefined });
      await reload();
    } catch (err) { setMsg(err.message); }
  }
  async function retire(id) {
    setMsg(null);
    try { await send(`blocks/settings/execution-accounts/${id}`, { status: "retired" }, "PATCH"); await reload(); } catch (err) { setMsg(err.message); }
  }
  return (
    <div className="space-y-5">
      <div className={`${card} overflow-x-auto`}>
        <table className="w-full text-sm">
          <thead className="border-b border-[#ece8dd] text-left text-text-muted">
            <tr><th className="px-4 py-3 font-medium">Broker</th><th className="px-4 py-3 font-medium">Account</th><th className="px-4 py-3 font-medium">DVP</th><th className="px-4 py-3 font-medium">Contact</th><th className="px-4 py-3 font-medium">Status</th><th /></tr>
          </thead>
          <tbody>{rows.map((r) => (
            <tr key={r.id} className="border-b border-[#ece8dd] last:border-0">
              <td className="px-4 py-3">{r.broker_name}</td>
              <td className="px-4 py-3">{r.account_reference_masked}</td>
              <td className="px-4 py-3">{r.dvp_capable ? "DVP-capable" : "Not DVP"}</td>
              <td className="px-4 py-3">{r.contact || "—"}</td>
              <td className="px-4 py-3">{labelOf(v.execution_account_statuses, r.status)}</td>
              <td className="px-4 py-3">{c.edit && r.status === "active" ? <button type="button" className={secondary} onClick={() => retire(r.id)}>Retire</button> : null}</td>
            </tr>
          ))}</tbody>
        </table>
        {!rows.length ? <p className="p-10 text-center text-sm text-text-muted">No executing accounts recorded.</p> : null}
      </div>
      {c.add ? (
        <div className={`${card} flex flex-wrap items-end gap-2 p-6 text-sm`}>
          <label className="flex flex-col gap-1">Broker<input className={input} value={f.broker} onChange={(e) => setF({ ...f, broker: e.target.value })} /></label>
          <label className="flex flex-col gap-1">Account (last four only)<input className={`${input} w-28`} maxLength={4} autoComplete="off" value={f.last4} onChange={(e) => setF({ ...f, last4: e.target.value })} /></label>
          <label className="flex flex-col gap-1">Contact<input className={input} value={f.contact} onChange={(e) => setF({ ...f, contact: e.target.value })} /></label>
          <label className="flex items-center gap-2"><input type="checkbox" checked={f.dvp} onChange={(e) => setF({ ...f, dvp: e.target.checked })} />DVP-capable</label>
          <button type="button" className={primary} onClick={add}>Add executing account</button>
          {msg ? <span className="text-[#9B2335]">{msg}</span> : null}
        </div>
      ) : null}
    </div>
  );
}
