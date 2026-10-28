"use client";

/**
 * ProposalLotMethod — on a draft (or awaiting-approval) proposal, choose the
 * LOT METHOD for this proposal only, or pick the lots yourself ('specific').
 * The server makes a NEW proposal with it (the old one superseded), which a
 * different person approves. The control renders only when the proposal's own
 * envelope lists `override_lot_method` AND can_write is exactly true; the lot
 * picker only when the lots envelope lists it AND "lots" is editable.
 */

import { useState } from "react";
import {
  labelOf, methodOptions, needsTarget, overrideBody, overrideControl, pickerControl, preferenceOptions, vocab,
} from "@/lib/lotMethod.mjs";

const input = "rounded border border-[#E2E8F0] px-3 py-2";
const primary = "rounded bg-navy px-3 py-1 text-white disabled:opacity-50";
const secondary = "rounded border border-navy px-3 py-1 text-navy disabled:opacity-50";

export function LotPicker({ lotsEnvelope, picks, onPick }) {
  if (!pickerControl(lotsEnvelope)) return null;
  const lines = Array.isArray(lotsEnvelope?.lines) ? lotsEnvelope.lines : [];
  return (
    <div className="mt-2 space-y-3">
      {lines.map((ln) => (
        <div key={ln.global_security_id}>
          <p className="text-sm text-navy">{ln.label || ln.global_security_id} — this sale: {ln.line_quantity ?? "—"} sh</p>
          <table className="w-full text-xs">
            <thead className="text-left text-text-muted"><tr><th>Acquired</th><th>Held</th><th>Unit cost</th><th>Term</th><th>Est. gain</th><th>Sell</th></tr></thead>
            <tbody>{(ln.lots || []).map((l) => {
              const k = `${ln.global_security_id}|${l.lot_id}`;
              return (
                <tr key={l.lot_id} className="border-t border-[#ece8dd]">
                  <td>{l.acquired_date || "—"}</td><td>{l.quantity}</td><td>{l.unit_cost ?? "unknown"}</td><td>{l.term}</td>
                  <td>{l.est_gain ?? "—"}</td>
                  <td><input className={`${input} w-24 py-1`} aria-label={`quantity for lot ${l.lot_id}`} value={picks[k] || ""} onChange={(e) => onPick(k, e.target.value)} /></td>
                </tr>
              );
            })}</tbody>
          </table>
        </div>
      ))}
    </div>
  );
}

export default function ProposalLotMethod({ envelope, initialLots = null }) {
  const [open, setOpen] = useState(false);
  const [lots, setLots] = useState(initialLots);
  const [form, setForm] = useState({ lot_method: "", gain_preference: "", realize_gains_target: "", picks: {} });
  const [msg, setMsg] = useState(null);
  if (!overrideControl(envelope)) return null;
  const v = vocab(envelope).lot_method || {};
  const env = { vocabularies: v };
  const p = envelope?.proposal || {};
  const set = (k, val) => setForm({ ...form, [k]: val });

  async function openPicker() {
    setOpen(true);
    if (lots) return;
    const res = await fetch(`/api/lot-method/proposals/${p.id}/lots`);
    const data = await res.json().catch(() => ({}));
    if (res.ok) setLots(data); else setMsg(data.error || `Could not load the lots (${res.status})`);
  }
  async function submit() {
    setMsg(null);
    const res = await fetch(`/api/lot-method/proposals/${p.id}/override`, {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(overrideBody(form)),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) { setMsg(data.error || `Refused (${res.status})`); return; }
    window.location.href = `/portfolio/proposals/${data.proposal.id}`;
  }

  return (
    <div className="text-sm">
      <p className="text-text-muted">Lot method on this proposal: {p.lot_method ? labelOf(v.methods, p.lot_method) : "—"}
        {p.lot_method_source ? ` (${labelOf(v.sources, p.lot_method_source)})` : ""}</p>
      {!open ? <button type="button" className={`${secondary} mt-2`} onClick={openPicker}>Choose the lot method</button> : (
        <div className="mt-2 space-y-2">
          <div className="flex flex-wrap gap-2">
            <select className={input} value={form.lot_method} onChange={(e) => set("lot_method", e.target.value)}>
              <option value="">Method: keep</option>
              {methodOptions(env).map((m) => <option key={m.key} value={m.key}>{m.label}</option>)}
            </select>
            <select className={input} value={form.gain_preference} onChange={(e) => set("gain_preference", e.target.value)}>
              <option value="">Preference: keep</option>
              {preferenceOptions(env).map((m) => <option key={m.key} value={m.key}>{m.label}</option>)}
            </select>
            {needsTarget(form.gain_preference) ? <input className={input} placeholder="Target gain ($, you enter it)" value={form.realize_gains_target} onChange={(e) => set("realize_gains_target", e.target.value)} /> : null}
          </div>
          {form.lot_method === "specific" && lots ? (
            <LotPicker lotsEnvelope={lots} picks={form.picks} onPick={(k, q) => set("picks", { ...form.picks, [k]: q })} />
          ) : null}
          <p className="text-xs text-text-muted">A new proposal replaces this one; a different person approves it. {v.precedence ? `Precedence: ${v.precedence}.` : ""}</p>
          <button type="button" className={primary} disabled={!form.lot_method && !form.gain_preference} onClick={submit}>Re-make the proposal</button>
        </div>
      )}
      {msg ? <p className="mt-2 text-[#9B2335]">{msg}</p> : null}
    </div>
  );
}
