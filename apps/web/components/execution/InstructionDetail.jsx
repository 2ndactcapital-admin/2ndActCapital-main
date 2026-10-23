"use client";

/**
 * InstructionDetail — one execution instruction (REP2): the terms, the status
 * timeline, fills, reconcile, the letter of instruction, and the human-task
 * panel. Every control renders only from `instructionControls(envelope)` —
 * the server's own flags AND its `actions` list for this caller and status.
 * Approve / Reject: only with approve_execution, an open task, and never for a
 * maker of the instruction. Labels are the envelope's vocabularies.
 */

import { useState } from "react";
import { amountText, decimalString, instructionControls, labelOf, priceText, securityText, vocab } from "@/lib/execution.mjs";

const card = "rounded-[6px] border border-[#ece8dd] bg-white";
const input = "rounded border border-[#E2E8F0] px-3 py-2";
const primary = "rounded bg-navy px-3 py-1 text-white disabled:opacity-50";
const secondary = "rounded border border-navy px-3 py-1 text-navy disabled:opacity-50";

async function call(path, body, method = "POST") {
  const res = await fetch(`/api/execution/${path}`, {
    method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
  return data;
}

export function Summary({ envelope }) {
  const d = envelope?.instruction || {};
  const v = vocab(envelope);
  const rows = [
    ["Status", labelOf(v.statuses, d.status)],
    ["Account", `${d.account?.account_number_masked || ""}${d.account?.is_held_away ? " (held away)" : ""}`],
    ["Instruction", `${labelOf(v.sides, d.side)} ${amountText(d)}`],
    ["Security", securityText(d)],
    ["Price", priceText(d, v.price_kinds)],
    ["Carried out by", `${labelOf(v.executor_types, d.executor_type)}${d.executor_name ? ` — ${d.executor_name}` : ""}${d.executor_contact ? ` (${d.executor_contact})` : ""}`],
    ["Execute by", d.execute_by],
    ["If not filled", labelOf(v.if_not_filled, d.if_not_filled)],
    ["Filled so far", d.filled_quantity],
    ["Routed", d.routing_reason],
    ["Wash-sale notes", d.wash_sale_notes || "—"],
  ];
  return (
    <div className={`${card} p-6`}>
      <dl className="grid grid-cols-1 gap-x-6 gap-y-2 text-sm md:grid-cols-2">
        {rows.map(([k, val]) => (
          <div key={k} className="flex gap-3"><dt className="w-36 shrink-0 text-text-muted">{k}</dt><dd>{val ?? "—"}</dd></div>
        ))}
      </dl>
      {Array.isArray(d.lots) && d.lots.length ? (
        <div className="mt-4 text-sm">
          <p className="font-medium text-navy">Specific lots</p>
          <ul className="mt-1">{d.lots.map((l) => (
            <li key={l.id}>{l.quantity} sh of the lot acquired {l.acquired_date || "—"}{l.term ? ` (${l.term}-term)` : ""}</li>
          ))}</ul>
        </div>
      ) : null}
    </div>
  );
}

export function Timeline({ envelope }) {
  const d = envelope?.instruction || {};
  const v = vocab(envelope);
  const items = Array.isArray(d.timeline) ? d.timeline : [];
  return (
    <div className={`${card} p-6`}>
      <h2 className="text-lg font-semibold text-navy">Timeline</h2>
      <ol className="mt-3 space-y-2 text-sm">
        {items.map((e) => (
          <li key={e.id} className="flex gap-3">
            <span className="w-40 shrink-0 text-text-muted">{String(e.at).slice(0, 16).replace("T", " ")}</span>
            <span>
              <span className="font-medium text-navy">{labelOf(v.actions, e.action)}</span>
              {e.from_status !== e.to_status ? ` — ${labelOf(v.statuses, e.to_status)}` : ""}
              <span className="text-text-muted"> · {e.actor_name}</span>
              {e.detail?.reason || e.detail?.note ? <span className="block text-text-muted">{e.detail.reason || e.detail.note}</span> : null}
            </span>
          </li>
        ))}
      </ol>
    </div>
  );
}

export function TaskPanel({ envelope, onDone }) {
  const d = envelope?.instruction || {};
  const c = instructionControls(envelope);
  const [note, setNote] = useState("");
  const [msg, setMsg] = useState(null);
  const tasks = Array.isArray(d.tasks) ? d.tasks : [];
  const open = tasks.find((t) => t.status === "open");
  async function decide(outcome) {
    setMsg(null);
    try { await call(`instructions/${d.id}/${outcome}`, { note: note || undefined }); onDone?.(); } catch (err) { setMsg(err.message); }
  }
  return (
    <div className={`${card} p-6`}>
      <h2 className="text-lg font-semibold text-navy">Approval</h2>
      {open ? (
        <p className="mt-2 text-sm">Awaiting a decision by a holder of {open.eligible_permission}. Opened by {open.maker_name || "a colleague"} — a different person must decide.</p>
      ) : (
        <p className="mt-2 text-sm text-text-muted">No approval is open.</p>
      )}
      {tasks.filter((t) => t.status !== "open").map((t) => (
        <p key={t.id} className="mt-1 text-sm text-text-muted">
          {t.status === "completed" ? `${t.outcome === "approve" ? "Approved" : "Rejected"} by ${t.completed_by_name || "a colleague"}` : `Withdrawn: ${t.cancel_reason || ""}`}
          {t.note ? ` — ${t.note}` : ""}
        </p>
      ))}
      {c.approve || c.reject ? (
        <div className="mt-3 flex flex-wrap items-center gap-2 text-sm">
          <input className={input} placeholder="Note (required to reject)" value={note} onChange={(e) => setNote(e.target.value)} />
          {c.approve ? <button type="button" className={primary} onClick={() => decide("approve")}>Approve</button> : null}
          {c.reject ? <button type="button" className={secondary} onClick={() => decide("reject")}>Reject</button> : null}
        </div>
      ) : null}
      {msg ? <p className="mt-2 text-sm text-[#9B2335]">{msg}</p> : null}
    </div>
  );
}

export function FillForm({ envelope, onDone }) {
  const d = envelope?.instruction || {};
  const c = instructionControls(envelope);
  const [f, setF] = useState({ quantity: "", price: "", filledAt: "", completes: false, lots: {} });
  const [msg, setMsg] = useState(null);
  if (!c.fill) return null;
  async function submit(e) {
    e.preventDefault();
    setMsg(null);
    try {
      await call(`instructions/${d.id}/fills`, {
        quantity: decimalString(f.quantity), price: decimalString(f.price),
        filled_at: f.filledAt ? new Date(f.filledAt).toISOString() : null, completes: f.completes,
        lots: Object.entries(f.lots).filter(([, q]) => decimalString(q)).map(([id, q]) => ({ tax_lot_id: id, quantity: decimalString(q) })),
      });
      onDone?.();
    } catch (err) { setMsg(err.message); }
  }
  return (
    <form onSubmit={submit} className="mt-3 flex flex-wrap items-end gap-3 text-sm">
      <label className="flex flex-col gap-1">Quantity<input className={input} inputMode="decimal" value={f.quantity} onChange={(e) => setF({ ...f, quantity: e.target.value })} /></label>
      <label className="flex flex-col gap-1">Price<input className={input} inputMode="decimal" value={f.price} onChange={(e) => setF({ ...f, price: e.target.value })} /></label>
      <label className="flex flex-col gap-1">Time<input type="datetime-local" className={input} value={f.filledAt} onChange={(e) => setF({ ...f, filledAt: e.target.value })} /></label>
      {(d.lots || []).map((l) => (
        <label key={l.tax_lot_id} className="flex flex-col gap-1">Lot {l.acquired_date || ""}
          <input className={`${input} w-28`} inputMode="decimal" value={f.lots[l.tax_lot_id] || ""}
            onChange={(e) => setF({ ...f, lots: { ...f.lots, [l.tax_lot_id]: e.target.value } })} />
        </label>
      ))}
      <label className="flex items-center gap-2"><input type="checkbox" checked={f.completes} onChange={(e) => setF({ ...f, completes: e.target.checked })} />Completes the order</label>
      <button type="submit" className={primary}>Enter fill</button>
      {msg ? <span className="text-[#9B2335]">{msg}</span> : null}
    </form>
  );
}

export function Fills({ envelope, onDone }) {
  const d = envelope?.instruction || {};
  const fills = Array.isArray(d.fills) ? d.fills : [];
  return (
    <div className={`${card} p-6`}>
      <h2 className="text-lg font-semibold text-navy">Fills</h2>
      {fills.length ? (
        <table className="mt-3 w-full text-sm">
          <thead className="text-left text-text-muted"><tr><th className="py-1">Time</th><th>Quantity</th><th>Price</th><th>Entered by</th></tr></thead>
          <tbody>{fills.map((x) => (
            <tr key={x.id}><td className="py-1">{String(x.filled_at).slice(0, 16).replace("T", " ")}</td><td>{x.quantity}</td><td>{x.price}</td><td>{x.entered_by_name}</td></tr>
          ))}</tbody>
        </table>
      ) : <p className="mt-2 text-sm text-text-muted">No fills entered.</p>}
      <FillForm envelope={envelope} onDone={onDone} />
    </div>
  );
}

export function ActionBar({ envelope, onDone }) {
  const d = envelope?.instruction || {};
  const v = vocab(envelope);
  const c = instructionControls(envelope);
  const [form, setForm] = useState({ channel: v.sent_channels?.[0]?.key || "", reason: "", method: "custodian_data", reference: "", note: "" });
  const [mode, setMode] = useState(null);
  const [msg, setMsg] = useState(null);
  async function run(path, body) {
    setMsg(null);
    try { await call(`instructions/${d.id}/${path}`, body); setMode(null); onDone?.(); } catch (err) { setMsg(err.message); }
  }
  const any = c.submit || c.markSent || c.reconcile || c.close || c.cancel;
  if (!any) return null;
  return (
    <div className={`${card} p-4 text-sm`}>
      <div className="flex flex-wrap gap-2">
        {c.submit ? <button type="button" className={primary} onClick={() => run("submit", {})}>Submit for approval</button> : null}
        {c.markSent ? <button type="button" className={primary} onClick={() => setMode("sent")}>Mark sent</button> : null}
        {c.reconcile ? <button type="button" className={primary} onClick={() => setMode("reconcile")}>Reconcile</button> : null}
        {c.close ? <button type="button" className={primary} onClick={() => run("close", {})}>Close</button> : null}
        {c.cancel ? <button type="button" className={secondary} onClick={() => setMode("cancel")}>Cancel instruction</button> : null}
      </div>
      {mode === "sent" ? (
        <div className="mt-3 flex flex-wrap items-end gap-2">
          <select className={input} value={form.channel} onChange={(e) => setForm({ ...form, channel: e.target.value })}>
            {(v.sent_channels || []).map((s) => <option key={s.key} value={s.key}>{s.label}</option>)}
          </select>
          <input className={input} placeholder="Note" value={form.note} onChange={(e) => setForm({ ...form, note: e.target.value })} />
          <button type="button" className={primary} onClick={() => run("sent", { channel: form.channel, note: form.note || undefined })}>Confirm sent</button>
        </div>
      ) : null}
      {mode === "reconcile" ? (
        <div className="mt-3 flex flex-wrap items-end gap-2">
          <select className={input} value={form.method} onChange={(e) => setForm({ ...form, method: e.target.value })}>
            {(v.reconcile_methods || []).map((s) => <option key={s.key} value={s.key}>{s.label}</option>)}
          </select>
          <input className={input} placeholder="Custodian reference" value={form.reference} onChange={(e) => setForm({ ...form, reference: e.target.value })} />
          <input className={input} placeholder="Note" value={form.note} onChange={(e) => setForm({ ...form, note: e.target.value })} />
          <button type="button" className={primary} onClick={() => run("reconcile", { method: form.method, reference: form.reference || undefined, note: form.note || undefined })}>Confirm reconciled</button>
        </div>
      ) : null}
      {mode === "cancel" ? (
        <div className="mt-3 flex flex-wrap items-end gap-2">
          <input className={input} placeholder="Reason (required)" value={form.reason} onChange={(e) => setForm({ ...form, reason: e.target.value })} />
          <button type="button" className={secondary} onClick={() => run("cancel", { reason: form.reason })}>Confirm cancel</button>
        </div>
      ) : null}
      {msg ? <p className="mt-2 text-[#9B2335]">{msg}</p> : null}
    </div>
  );
}

export function EditDraft({ envelope, onDone }) {
  const d = envelope?.instruction || {};
  const c = instructionControls(envelope);
  const v = vocab(envelope);
  const editable = Array.isArray(v.editable) ? v.editable : [];
  const [f, setF] = useState({ amount: d.quantity ?? d.notional ?? "", executeBy: d.execute_by || "",
    executorName: d.executor_name || "", executorContact: d.executor_contact || "", notes: d.notes || "" });
  const [msg, setMsg] = useState(null);
  if (!c.edit || !editable.length) return null;
  async function save(e) {
    e.preventDefault();
    setMsg(null);
    const body = { execute_by: f.executeBy, executor_name: f.executorName || null,
      executor_contact: f.executorContact || null, notes: f.notes || null };
    body[d.quantity !== null && d.quantity !== undefined ? "quantity" : "notional"] = decimalString(f.amount);
    try { await call(`instructions/${d.id}`, body, "PATCH"); onDone?.(); } catch (err) { setMsg(err.message); }
  }
  return (
    <form onSubmit={save} className={`${card} flex flex-wrap items-end gap-3 p-4 text-sm`}>
      <span className="w-full font-medium text-navy">Edit draft</span>
      <label className="flex flex-col gap-1">{d.quantity !== null && d.quantity !== undefined ? "Shares" : "Notional"}
        <input className={input} inputMode="decimal" value={f.amount} onChange={(e) => setF({ ...f, amount: e.target.value })} /></label>
      <label className="flex flex-col gap-1">Execute by
        <input type="date" className={input} value={f.executeBy} onChange={(e) => setF({ ...f, executeBy: e.target.value })} /></label>
      <label className="flex flex-col gap-1">Executor name
        <input className={input} value={f.executorName} onChange={(e) => setF({ ...f, executorName: e.target.value })} /></label>
      <label className="flex flex-col gap-1">Executor contact
        <input className={input} value={f.executorContact} onChange={(e) => setF({ ...f, executorContact: e.target.value })} /></label>
      <label className="flex flex-col gap-1">Notes
        <input className={input} value={f.notes} onChange={(e) => setF({ ...f, notes: e.target.value })} /></label>
      <button type="submit" className={primary}>Save draft</button>
      {msg ? <span className="text-[#9B2335]">{msg}</span> : null}
    </form>
  );
}

export function LetterPanel({ envelope }) {
  const d = envelope?.instruction || {};
  const c = instructionControls(envelope);
  const [full, setFull] = useState(false);
  const [number, setNumber] = useState("");
  const [msg, setMsg] = useState(null);
  if (!c.letter) return null;
  async function download() {
    setMsg(null);
    const res = await fetch(`/api/execution/instructions/${d.id}/letter`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(full ? { include_full_number: true, full_account_number: number } : {}),
    });
    if (!res.ok) { const data = await res.json().catch(() => ({})); setMsg(data.error || "Could not generate the letter"); return; }
    const url = URL.createObjectURL(await res.blob());
    const a = document.createElement("a");
    a.href = url; a.download = `letter-of-instruction-${String(d.id).slice(0, 8)}.pdf`; a.click();
    URL.revokeObjectURL(url);
    setNumber("");
  }
  return (
    <div className={`${card} p-6 text-sm`}>
      <h2 className="text-lg font-semibold text-navy">Letter of instruction</h2>
      <p className="mt-1 text-text-muted">Generated from this instruction when you ask for it. The account number is masked unless you choose otherwise; that choice is recorded.</p>
      <label className="mt-3 flex items-center gap-2"><input type="checkbox" checked={full} onChange={(e) => setFull(e.target.checked)} />Include the full account number</label>
      {full ? <input className={`${input} mt-2`} autoComplete="off" placeholder="Type the full account number" value={number} onChange={(e) => setNumber(e.target.value)} /> : null}
      <div className="mt-3"><button type="button" className={primary} onClick={download}>Download letter</button></div>
      {msg ? <p className="mt-2 text-[#9B2335]">{msg}</p> : null}
    </div>
  );
}

export default function InstructionDetail({ initialEnvelope }) {
  const [env, setEnv] = useState(initialEnvelope);
  const id = env?.instruction?.id;
  async function reload() {
    const res = await fetch(`/api/execution/instructions/${id}`);
    const data = await res.json().catch(() => ({}));
    if (res.ok) setEnv(data);
  }
  return (
    <div className="space-y-5">
      <Summary envelope={env} />
      <ActionBar envelope={env} onDone={reload} />
      <EditDraft envelope={env} onDone={reload} />
      <TaskPanel envelope={env} onDone={reload} />
      <Fills envelope={env} onDone={reload} />
      <LetterPanel envelope={env} />
      <Timeline envelope={env} />
    </div>
  );
}
