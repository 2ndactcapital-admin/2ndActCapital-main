"use client";

/**
 * InstructionDetail — one execution instruction (REP2): the terms, the status
 * timeline, fills, reconcile, the letter of instruction, and the human-task
 * panel. Every control renders only from `instructionControls(envelope)` —
 * the server's own flags AND its `actions` list for this caller and status.
 * Approve / Reject: only with approve_execution, an open task, and never for a
 * maker of the instruction. Labels are the envelope's vocabularies.
 */

import { useEffect, useState } from "react";
import { BusyButton, BusyError, useBusy } from "@/components/ui/Busy";
import {
  amountText, custodianModel, decimalString, instructionControls, labelOf, manualChannels, orderExecutions, priceText,
  reconcilePicker, securityText, vocab,
} from "@/lib/execution.mjs";

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
            <tr key={x.id}><td className="py-1">{String(x.filled_at).slice(0, 16).replace("T", " ")}</td><td>{x.quantity}</td><td>{x.price}</td><td>{x.entered_by_name}{x.custodian_exec_id ? <SimulatedBadge /> : null}</td></tr>
          ))}</tbody>
        </table>
      ) : <p className="mt-2 text-sm text-text-muted">No fills entered.</p>}
      <FillForm envelope={envelope} onDone={onDone} />
    </div>
  );
}

/** CPSIM03 — shown wherever an order of a SIMULATED custodian appears. */
export function SimulatedBadge() {
  return (
    <span data-testid="simulated-badge" className="ml-2 rounded border border-[#C5A880] bg-[#F5F1EB] px-2 py-0.5 text-xs font-semibold tracking-wide text-navy">
      SIMULATED
    </span>
  );
}

/**
 * CPSIM03 — the custodian-API route: "Send to custodian (simulated)" only when the server lists it (D2 holds), else
 * the reason the instruction is manual; the custodian order's status and executions (read live), "Import fills".
 * A person presses each button — nothing here runs on its own.
 */
export function CustodianOrderPanel({ envelope, orderView, setOrderView, onDone }) {
  const d = envelope?.instruction || {};
  const c = instructionControls(envelope);
  const m = custodianModel(envelope);
  const [msg, setMsg] = useState(null);
  const action = useBusy(); // CATCHALL1 busy — Send to custodian / Import fills
  useEffect(() => {
    if (!m.orderId || orderView || !setOrderView) return;
    fetch(`/api/execution/instructions/${d.id}/custodian-order`).then((r) => r.json().then((j) => (r.ok ? j : null)))
      .then((j) => j && setOrderView(j)).catch(() => {});
  }, [m.orderId, d.id, orderView, setOrderView]);
  if (!m.simulated && !m.sendRefused) return null;
  const run = (path) => action.run(async () => {
    setMsg(null);
    const out = await call(`instructions/${d.id}/${path}`, {});
    if (out.outcome === "rejected") setMsg(`Refused by the custodian: ${out.reason}`);
    if (setOrderView) setOrderView(null);
    onDone?.();
  });
  const execs = orderExecutions(orderView);
  const order = orderView?.order || null;
  return (
    <div className={`${card} p-6 text-sm`} data-testid="custodian-panel">
      <h2 className="text-lg font-semibold text-navy">Custodian order{m.simulated ? <SimulatedBadge /> : null}</h2>
      {m.simulated ? (
        <p className="mt-1 text-text-muted">Routed to the simulated custodian {m.counterparty || ""}. A person sends it after approval and imports the fills; nothing is sent or booked on its own.</p>
      ) : (
        <p className="mt-1 text-text-muted" data-testid="custodian-manual-reason">Manual: {m.sendRefused}</p>
      )}
      {m.simulated && m.sendRefused && !c.sendToCustodian ? (
        <p className="mt-1" data-testid="custodian-send-refused">Send to custodian is not available: {m.sendRefused}</p>
      ) : null}
      <div className="mt-3 flex flex-wrap gap-2">
        {c.sendToCustodian ? <BusyButton busy={action.busy} busyLabel="Sending…" className={primary} onClick={() => run("send-to-custodian")}>Send to custodian (simulated)</BusyButton> : null}
        {c.importFills ? <BusyButton busy={action.busy} busyLabel="Importing…" className={primary} onClick={() => run("import-fills")}>Import fills</BusyButton> : null}
      </div>
      <BusyError error={action.error} className="mt-2 block text-[#9B2335]" />
      {order ? (
        <div className="mt-3" data-testid="custodian-order">
          <p>Order {order.order_id} — <span className="font-medium text-navy">{order.status}</span>{order.reject_reason ? ` (${order.reject_reason})` : ""} · filled {order.filled_quantity}</p>
          {execs.length ? (
            <table className="mt-2 w-full text-left">
              <thead className="text-text-muted"><tr><th className="py-1">Execution</th><th>Quantity</th><th>Price</th><th>Trade date</th><th>Settles</th><th>Booked</th></tr></thead>
              <tbody>{execs.map((e) => (
                <tr key={e.exec_id}><td className="py-1">{e.exec_id}</td><td>{e.quantity}</td><td>{e.price}</td><td>{e.trade_date}</td><td>{e.settle_date}</td><td>{e.booked ? "Booked" : "Not yet imported"}</td></tr>
              ))}</tbody>
            </table>
          ) : <p className="mt-1 text-text-muted">No executions yet.</p>}
        </div>
      ) : m.orderId ? <p className="mt-2 text-text-muted">Order {m.orderId}</p> : null}
      {msg ? <p className="mt-2 text-[#9B2335]">{msg}</p> : null}
    </div>
  );
}

export function ActionBar({ envelope, onDone, orderView = null }) {
  const d = envelope?.instruction || {};
  const v = vocab(envelope);
  const c = instructionControls(envelope);
  const channels = manualChannels(v);
  const picker = reconcilePicker(orderView);
  const [form, setForm] = useState({ channel: channels[0]?.key || "", reason: "", method: "custodian_data", reference: picker.reference, note: "" });
  const [picked, setPicked] = useState(() => new Set(picker.rows.filter((r) => r.checked).map((r) => r.id)));
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
            {channels.map((s) => <option key={s.key} value={s.key}>{s.label}</option>)}
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
          <button type="button" className={primary} onClick={() => run("reconcile", { method: form.method, reference: form.reference || undefined, note: form.note || undefined, transaction_ids: form.method === "custodian_data" ? [...picked] : undefined })}>Confirm reconciled</button>
        </div>
      ) : null}
      {mode === "reconcile" && picker.rows.length ? (
        <ReconcilePicker rows={picker.rows} picked={picked} setPicked={setPicked} />
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

/** CPSIM03 — the synced custodian transactions of this order (preselected by the server's same-security rule). */
export function ReconcilePicker({ rows, picked, setPicked }) {
  return (
    <fieldset className="mt-3" data-testid="reconcile-picker">
      <legend className="text-text-muted">Synced custodian transactions<SimulatedBadge /></legend>
      {rows.map((r) => (
        <label key={r.id} className="mt-1 flex items-center gap-2">
          <input type="checkbox" checked={picked.has(r.id)} data-txn={r.id}
            onChange={(e) => { const n = new Set(picked); if (e.target.checked) n.add(r.id); else n.delete(r.id); setPicked(n); }} />
          {r.external_ref} · {r.trade_date} · {r.quantity} @ {r.price}
        </label>
      ))}
    </fieldset>
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
  const letter = useBusy(); // CATCHALL1 busy — Download letter
  if (!c.letter) return null;
  const download = () => letter.run(async () => {
    const res = await fetch(`/api/execution/instructions/${d.id}/letter`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(full ? { include_full_number: true, full_account_number: number } : {}),
    });
    if (!res.ok) { const data = await res.json().catch(() => ({})); throw new Error(data.error || "Could not generate the letter"); }
    const url = URL.createObjectURL(await res.blob());
    const a = document.createElement("a");
    a.href = url; a.download = `letter-of-instruction-${String(d.id).slice(0, 8)}.pdf`; a.click();
    URL.revokeObjectURL(url);
    setNumber("");
  });
  return (
    <div className={`${card} p-6 text-sm`}>
      <h2 className="text-lg font-semibold text-navy">Letter of instruction</h2>
      <p className="mt-1 text-text-muted">Generated from this instruction when you ask for it. The account number is masked unless you choose otherwise; that choice is recorded.</p>
      <label className="mt-3 flex items-center gap-2"><input type="checkbox" checked={full} onChange={(e) => setFull(e.target.checked)} />Include the full account number</label>
      {full ? <input className={`${input} mt-2`} autoComplete="off" placeholder="Type the full account number" value={number} onChange={(e) => setNumber(e.target.value)} /> : null}
      <div className="mt-3"><BusyButton busy={letter.busy} busyLabel="Generating…" className={primary} onClick={download}>Download letter</BusyButton></div>
      <BusyError error={letter.error} className="mt-2 block text-[#9B2335]" />
    </div>
  );
}

/**
 * REP9 — "Print order execution sheet" on every broker / desk / trader
 * instruction: the manual route is always offered. When an automated route is
 * available for the account the server asks why manual was chosen; the choice
 * and the reason are recorded on the timeline.
 */
export function OrderSheetPanel({ envelope }) {
  const d = envelope?.instruction || {};
  const c = instructionControls(envelope);
  const [reason, setReason] = useState("");
  const sheet = useBusy(); // CATCHALL1 busy — Print order execution sheet
  if (!c.orderSheet) return null;
  const download = () => sheet.run(async () => {
    const res = await fetch(`/api/execution/blocks/instructions/${d.id}/sheet`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(reason ? { manual_reason: reason } : {}),
    });
    if (!res.ok) { const data = await res.json().catch(() => ({})); throw new Error(data.error || "Could not generate the sheet"); }
    const url = URL.createObjectURL(await res.blob());
    const a = document.createElement("a");
    a.href = url; a.download = `order-execution-sheet-${String(d.id).slice(0, 8)}.pdf`; a.click();
    URL.revokeObjectURL(url);
  });
  return (
    <div className={`${card} p-6 text-sm`}>
      <h2 className="text-lg font-semibold text-navy">Order execution sheet</h2>
      <p className="mt-1 text-text-muted">Print it and give it to the broker of your choice; enter the fills by hand. Masked account reference, no member names.</p>
      {d.block ? <p className="mt-1">In block <a className="text-navy underline" href={`/portfolio/blocks/${d.block.block_id}`}>{String(d.block.block_id).slice(0, 8)}</a>.</p> : null}
      <input className={`${input} mt-2`} placeholder="Why manual (asked when an automated route is available)" value={reason} onChange={(e) => setReason(e.target.value)} />
      <div className="mt-3"><BusyButton busy={sheet.busy} busyLabel="Generating…" className={primary} onClick={download}>Print order execution sheet</BusyButton></div>
      <BusyError error={sheet.error} className="mt-2 block text-[#9B2335]" />
    </div>
  );
}

/**
 * CPSIM05 — the FIX exchange of an instruction routed to the SIMULATED FIX counterparty: every message its steps sent
 * and received (from its own events), readable tag / name / value, and "automatic — simulated counterparty" when the
 * proposal's approval sent it. Read-only: there is no button here.
 */
export function FixExchangePanel({ envelope }) {
  const fx = envelope?.instruction?.fix_exchange;
  if (!fx) return null;
  const msgs = Array.isArray(fx.messages) ? fx.messages : [];
  return (
    <div className={`${card} p-6 text-sm`} data-testid="fix-exchange">
      <h2 className="text-lg font-semibold text-navy">FIX exchange<SimulatedBadge /></h2>
      {fx.automatic_label ? <p className="mt-1 text-[#334155]" data-testid="fix-automatic">{fx.automatic_label}: sent,
        booked and reconciled after the proposal&apos;s maker-checker approval — no second approval.</p> : null}
      {msgs.length ? (
        <ol className="mt-2 space-y-2">{msgs.map((m, i) => (
          <li key={`${m.event_id}-${m.direction}-${i}`} data-fix-direction={m.direction}>
            <p className="font-medium text-navy">{m.direction === "out" ? "Sent" : "Received"} — {m.msg_name || m.msg_type}
              <span className="ml-2 text-xs text-[#64748B]">{m.action}</span></p>
            <ul className="text-xs text-[#334155]">{(m.fields || []).filter((f) => ![8, 9, 10].includes(f.tag)).map((f, j) => (
              <li key={`${f.tag}-${j}`}>{f.tag} {f.name}: {f.value}</li>))}</ul>
            <code className="block break-all text-[10px] text-[#64748B]">{m.raw}</code>
          </li>))}</ol>
      ) : <p className="mt-2 text-[#64748B]" data-testid="fix-exchange-none">No FIX message yet.</p>}
    </div>
  );
}

export default function InstructionDetail({ initialEnvelope, initialOrderView = null }) {
  const [env, setEnv] = useState(initialEnvelope);
  const [orderView, setOrderView] = useState(initialOrderView);
  const id = env?.instruction?.id;
  async function reload() {
    const res = await fetch(`/api/execution/instructions/${id}`);
    const data = await res.json().catch(() => ({}));
    if (res.ok) setEnv(data);
  }
  return (
    <div className="space-y-5">
      <Summary envelope={env} />
      <CustodianOrderPanel envelope={env} orderView={orderView} setOrderView={setOrderView} onDone={reload} />
      <FixExchangePanel envelope={env} />
      <ActionBar key={`${env?.instruction?.status}-${orderView ? "o" : "n"}`} envelope={env} onDone={reload} orderView={orderView} />
      <EditDraft envelope={env} onDone={reload} />
      <TaskPanel envelope={env} onDone={reload} />
      <Fills envelope={env} onDone={reload} />
      <LetterPanel envelope={env} />
      <OrderSheetPanel envelope={env} />
      <Timeline envelope={env} />
    </div>
  );
}
