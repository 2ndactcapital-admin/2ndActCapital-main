"use client";

/**
 * HypotheticalPolicy — Settings → Hypothetical-performance policy (REP10). The
 * firm's own words, who may see a backtest (staff only | staff and members
 * with the disclosure) and the disclosure itself; versioned, each version
 * approved by a second holder of manage_hypothetical_policy. With none
 * approved, backtests are staff-only. Counsel's open items are shown as notes.
 */

import { useState } from "react";
import { labelOf, policyControls, vocab } from "@/lib/rep10.mjs";

const card = "rounded-[6px] border border-[#ece8dd] bg-white";
const input = "rounded border border-[#E2E8F0] px-3 py-2";
const primary = "rounded bg-navy px-3 py-1 text-white disabled:opacity-50";
const secondary = "rounded border border-navy px-3 py-1 text-navy disabled:opacity-50";

export function PolicyView({ envelope, onRequest, onDecide }) {
  const v = vocab(envelope);
  const c = policyControls(envelope);
  const rows = Array.isArray(envelope?.rows) ? envelope.rows : [];
  const [f, setF] = useState({ policy_text: "", audience: "staff_only", disclosure_text: "", reason: "" });
  const [note, setNote] = useState("");
  const notes = Array.isArray(v.open_items) ? v.open_items : [];
  return (
    <div className="space-y-4">
      <p className={`${card} p-3 text-sm text-navy`}>In effect: {envelope?.gate?.disclosure || v.no_policy_label}</p>
      {c.request ? (
        <div className={`${card} space-y-2 p-4 text-sm`}>
          <textarea className={`${input} h-32 w-full`} placeholder="The firm's hypothetical-performance policy, in its own words" value={f.policy_text} onChange={(e) => setF({ ...f, policy_text: e.target.value })} />
          <textarea className={`${input} h-20 w-full`} placeholder="The disclosure every backtest carries" value={f.disclosure_text} onChange={(e) => setF({ ...f, disclosure_text: e.target.value })} />
          <div className="flex flex-wrap gap-2">
            <select className={input} value={f.audience} onChange={(e) => setF({ ...f, audience: e.target.value })}>
              {(v.audiences || []).map((a) => <option key={a.key} value={a.key}>{a.label}</option>)}
            </select>
            <input className={input} placeholder="Reason" value={f.reason} onChange={(e) => setF({ ...f, reason: e.target.value })} />
            <button type="button" className={primary} onClick={() => onRequest?.(f)}>Submit for approval</button>
          </div>
        </div>
      ) : null}
      {rows.map((r) => {
        const rc = policyControls(envelope, r);
        return (
          <div key={r.id} className={`${card} p-4 text-sm`}>
            <p className="font-medium text-navy">Version {r.version_no} — {labelOf(v.policy_statuses, r.status)}{r.in_effect ? " (in effect)" : ""} · {labelOf(v.audiences, r.audience)}</p>
            <p className="mt-1 whitespace-pre-wrap">{r.policy_text}</p>
            <p className="mt-1 text-text-muted">Disclosure: {r.disclosure_text}</p>
            {rc.approve || rc.reject ? (
              <div className="mt-2 flex gap-2">
                <input className={input} placeholder="Note (required to reject)" value={note} onChange={(e) => setNote(e.target.value)} />
                {rc.approve ? <button type="button" className={primary} onClick={() => onDecide?.(r, "approve", note)}>Approve</button> : null}
                {rc.reject ? <button type="button" className={secondary} onClick={() => onDecide?.(r, "reject", note)}>Reject</button> : null}
              </div>
            ) : null}
          </div>
        );
      })}
      {notes.length ? (
        <div className={`${card} p-4 text-sm`}>
          <h2 className="text-lg font-semibold text-navy">Open for the firm and counsel</h2>
          <ul className="mt-1 list-disc pl-5 text-text-muted">{notes.map((n) => <li key={n}>{n}</li>)}</ul>
        </div>
      ) : null}
    </div>
  );
}

export default function HypotheticalPolicy({ initialEnvelope }) {
  const [env, setEnv] = useState(initialEnvelope);
  const [msg, setMsg] = useState(null);
  async function post(path, body) {
    setMsg(null);
    const res = await fetch(`/api/replication/${path}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) { setMsg(data.error); return; }
    const r2 = await fetch("/api/replication/hypothetical-policy");
    if (r2.ok) setEnv(await r2.json());
  }
  return (
    <div className="space-y-3">
      {msg ? <p className="text-sm text-[#9B2335]">{msg}</p> : null}
      <PolicyView envelope={env} onRequest={(f) => post("hypothetical-policy", f)}
        onDecide={(r, o, note) => post(`hypothetical-policy/${r.id}/${o}`, { note: note || undefined })} />
    </div>
  );
}
