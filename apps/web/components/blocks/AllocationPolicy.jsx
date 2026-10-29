"use client";

/**
 * AllocationPolicy — Settings → Allocation policy (REP9).
 *
 * The firm's written aggregation and allocation policy, versioned: each new
 * version is the firm's own words (with its ADV reference) plus the mechanics
 * the platform enforces (method, minimum allocation, rounding and partial-fill
 * rules), and takes effect only when a second person approves it. Controls
 * render only from `policyControls` (the envelope's flags and its per-version
 * action list — fail closed). The compliance questions stay open as notes.
 */

import { useState } from "react";
import { labelOf, policyControls, vocab } from "@/lib/blocks.mjs";

const card = "rounded-[6px] border border-[#ece8dd] bg-white";
const input = "rounded border border-[#E2E8F0] px-3 py-2";
const primary = "rounded bg-navy px-3 py-1 text-white disabled:opacity-50";
const secondary = "rounded border border-navy px-3 py-1 text-navy disabled:opacity-50";

async function post(path, body) {
  const res = await fetch(`/api/execution/${path}`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
  return data;
}

export function PolicyVersion({ envelope, row, onDone }) {
  const v = vocab(envelope);
  const c = policyControls(envelope, row.id);
  const [note, setNote] = useState("");
  const [msg, setMsg] = useState(null);
  async function decide(outcome) {
    setMsg(null);
    try { await post(`blocks/settings/policies/${row.id}/${outcome}`, { note: note || undefined }); onDone?.(); } catch (err) { setMsg(err.message); }
  }
  return (
    <div className={`${card} p-6 text-sm`}>
      <p className="font-medium text-navy">Version {row.version_no} — {labelOf(v.policy_statuses, row.status)}{row.in_effect ? " (in effect)" : ""}</p>
      <p className="mt-1 text-text-muted">{labelOf(v.methods, row.method)} · minimum {row.minimum_allocation} · {labelOf(v.rounding_rules, row.rounding_rule)} · {labelOf(v.partial_fill_rules, row.partial_fill_rule)}</p>
      <p className="mt-2 whitespace-pre-wrap">{row.policy_text}</p>
      {row.adv_reference ? <p className="mt-1 text-text-muted">Form ADV: {row.adv_reference}</p> : null}
      {row.superseded_reason ? <p className="mt-1 text-text-muted">{row.superseded_reason}</p> : null}
      {c.approve || c.reject ? (
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <input className={input} placeholder="Note (required to reject)" value={note} onChange={(e) => setNote(e.target.value)} />
          {c.approve ? <button type="button" className={primary} onClick={() => decide("approve")}>Approve version</button> : null}
          {c.reject ? <button type="button" className={secondary} onClick={() => decide("reject")}>Reject version</button> : null}
        </div>
      ) : null}
      {msg ? <p className="mt-2 text-[#9B2335]">{msg}</p> : null}
    </div>
  );
}

export default function AllocationPolicy({ initialEnvelope }) {
  const [env, setEnv] = useState(initialEnvelope);
  const rows = Array.isArray(env?.rows) ? env.rows : [];
  const v = vocab(env);
  const c = policyControls(env, null);
  const [f, setF] = useState({ minimum: "0", text: "", adv: "", reason: "" });
  const [msg, setMsg] = useState(null);
  async function reload() {
    const res = await fetch("/api/execution/blocks/settings/policies");
    const data = await res.json().catch(() => ({}));
    if (res.ok) setEnv(data);
  }
  async function request() {
    setMsg(null);
    try {
      await post("blocks/settings/policies", { minimum_allocation: f.minimum.trim(), policy_text: f.text,
        adv_reference: f.adv || undefined, reason: f.reason });
      await reload();
    } catch (err) { setMsg(err.message); }
  }
  const notes = Array.isArray(v.compliance_notes) ? v.compliance_notes : [];
  return (
    <div className="space-y-5">
      {c.request ? (
        <div className={`${card} space-y-2 p-6 text-sm`}>
          <h2 className="text-lg font-semibold text-navy">New policy version</h2>
          <textarea className={`${input} h-40 w-full`} placeholder="The firm's written aggregation and allocation policy, in its own words" value={f.text} onChange={(e) => setF({ ...f, text: e.target.value })} />
          <div className="flex flex-wrap items-end gap-2">
            <label className="flex flex-col gap-1">Minimum allocation (shares)<input className={input} inputMode="decimal" value={f.minimum} onChange={(e) => setF({ ...f, minimum: e.target.value })} /></label>
            <label className="flex flex-col gap-1">Form ADV reference<input className={input} value={f.adv} onChange={(e) => setF({ ...f, adv: e.target.value })} /></label>
            <label className="flex flex-col gap-1">Reason<input className={input} value={f.reason} onChange={(e) => setF({ ...f, reason: e.target.value })} /></label>
            <button type="button" className={primary} onClick={request}>Submit for approval</button>
          </div>
          {msg ? <p className="text-[#9B2335]">{msg}</p> : null}
        </div>
      ) : null}
      {rows.length ? rows.map((r) => <PolicyVersion key={r.id} envelope={env} row={r} onDone={reload} />)
        : <div className={`${card} p-10 text-center text-sm text-text-muted`}>No allocation policy yet. No block can be built until one is approved.</div>}
      {notes.length ? (
        <div className={`${card} p-6 text-sm`}>
          <h2 className="text-lg font-semibold text-navy">Open for the firm and counsel</h2>
          <ul className="mt-2 list-disc space-y-1 pl-5 text-text-muted">{notes.map((n) => <li key={n}>{n}</li>)}</ul>
        </div>
      ) : null}
    </div>
  );
}
