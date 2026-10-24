"use client";

/**
 * DeclaredTrades — Portfolio → Tax → Declared outside trades (REP6): trades in
 * accounts outside the platform that the adviser declares, so they count in
 * the household's wash-sale windows (a sale with a realized amount also enters
 * the loss bank). Declare / Withdraw render only from the envelope.
 */

import { useState } from "react";
import { labelOf, money, pageControls, rowControls, subjectName, vocab } from "@/lib/tax.mjs";
import { SecurityPicker, card, input, post, primary, secondary } from "@/components/tax/common";

function DeclareForm({ envelope, subjects, onDone }) {
  const v = vocab(envelope);
  const [f, setF] = useState({ subject: ":", side: "sell", cls: "unknown", loss: "unknown" });
  const [sec, setSec] = useState(null);
  const [msg, setMsg] = useState(null);
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value });
  async function submit() {
    setMsg(null);
    const [t, i] = f.subject.split(":");
    const body = { subject_type: t, subject_id: i, account_description: f.desc, account_tax_class: f.cls,
      global_security_id: sec?.id, side: f.side, trade_date: f.date, source_note: f.note };
    if (f.qty) body.quantity = f.qty;
    if (f.side === "sell") {
      body.loss = f.loss;
      if (f.amount) { body.realized_amount = f.amount; body.term = f.term; }
    }
    try { await post("tax/declared-trades", body); setMsg("Declared — it counts in the household's windows now."); if (onDone) await onDone(); }
    catch (e) { setMsg(e.message); }
  }
  return (
    <div className="mt-3 grid grid-cols-2 gap-2 border-t border-[#ece8dd] pt-3">
      <p className="col-span-2 font-medium">Declare an outside trade</p>
      <select className={input} value={f.subject} onChange={set("subject")}>
        <option value=":">Household or member…</option>
        {(subjects || []).map((s) => <option key={`${s.subject_type}:${s.subject_id}`} value={`${s.subject_type}:${s.subject_id}`}>{s.name}</option>)}
      </select>
      <input className={input} placeholder="Outside account (e.g. spouse's 401(k) at another firm)" onChange={set("desc")} />
      <select className={input} value={f.cls} onChange={set("cls")}>
        {(v.tax_classes || []).map((s) => <option key={s.key} value={s.key}>{s.label}</option>)}
      </select>
      <select className={input} value={f.side} onChange={set("side")}>
        {(v.sides || []).map((s) => <option key={s.key} value={s.key}>{s.label}</option>)}
      </select>
      <div className="col-span-2"><SecurityPicker value={sec} onChange={setSec} /></div>
      <input className={input} type="date" onChange={set("date")} />
      <input className={input} placeholder="Quantity (optional)" onChange={set("qty")} />
      {f.side === "sell" ? (
        <>
          <select className={input} value={f.loss} onChange={set("loss")}>
            {(v.loss_values || []).map((s) => <option key={s.key} value={s.key}>{s.label}</option>)}
          </select>
          <input className={input} placeholder="Realized amount (optional, negative = loss)" onChange={set("amount")} />
          <select className={input} value={f.term || ""} onChange={set("term")}>
            <option value="">Term…</option>
            {(v.terms || []).map((s) => <option key={s.key} value={s.key}>{s.label}</option>)}
          </select>
        </>
      ) : null}
      <input className={`${input} col-span-2`} placeholder="Source note (who told us, when)" onChange={set("note")} />
      <div className="col-span-2"><button type="button" className={primary} onClick={submit}>Declare</button>
        {msg ? <span className="ml-2 text-sm text-text-muted">{msg}</span> : null}</div>
    </div>
  );
}

function Withdraw({ envelope, row, onDone }) {
  const [reason, setReason] = useState("");
  const [msg, setMsg] = useState(null);
  if (!rowControls(envelope, row).withdraw) return null;
  async function act() {
    setMsg(null);
    try { await post(`tax/declared-trades/${row.id}/withdraw`, { reason }); if (onDone) await onDone(); } catch (e) { setMsg(e.message); }
  }
  return (
    <span className="ml-2 inline-flex gap-2">
      <input className={input} placeholder="Reason" value={reason} onChange={(e) => setReason(e.target.value)} />
      <button type="button" className={secondary} onClick={act}>Withdraw</button>
      {msg ? <span className="text-[#9B2335]">{msg}</span> : null}
    </span>
  );
}

export default function DeclaredTrades({ envelope, subjects, onReload }) {
  const v = vocab(envelope);
  const c = pageControls(envelope);
  const rows = Array.isArray(envelope?.rows) ? envelope.rows : [];
  return (
    <div className={`${card} p-6 text-sm`}>
      <h2 className="text-lg font-semibold text-navy">Declared outside trades</h2>
      <p className="mt-1 text-text-muted">Trades outside the platform that count in the household's wash-sale windows.</p>
      {rows.length ? (
        <ul className="mt-2 space-y-2">{rows.map((r) => (
          <li key={r.id} className="border-t border-[#ece8dd] pt-2">
            {r.trade_date} · {labelOf(v.sides, r.side)} {r.quantity ? `${r.quantity} sh ` : ""}{r.security || r.global_security_id} · {r.account_description} ({labelOf(v.tax_classes, r.account_tax_class)}) · {subjectName(subjects, r.subject_type, r.subject_id)}
            {r.side === "sell" ? ` · ${labelOf(v.loss_values, r.loss)}${r.realized_amount ? ` ${money(r.realized_amount)} ${labelOf(v.terms, r.term)}` : ""}` : ""}
            <span className="block text-xs text-text-muted">Source: {r.source_note}</span>
            <Withdraw envelope={envelope} row={r} onDone={onReload} />
          </li>
        ))}</ul>
      ) : <p className="mt-2 text-text-muted">No declared trade.</p>}
      {c.declare ? <DeclareForm envelope={envelope} subjects={subjects} onDone={onReload} /> : null}
    </div>
  );
}
