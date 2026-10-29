"use client";

/**
 * BlockDetail — one trade block (REP9): its terms and price guidance (source
 * and as-of), the allocation table (fixed at approval; final after
 * allocation), fills and the computed average price, settlement status per
 * allocation, corrections, charges, the order execution sheet and the
 * timeline. Every control renders only from `blockControls(envelope)` — the
 * server's own flags AND its `actions` list for this caller and status.
 * Approve / Reject: only with approve_blocks, an open task, never a maker.
 */

import { useState } from "react";
import { blockControls, guidanceText, labelOf, settlementText, vocab } from "@/lib/blocks.mjs";

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

export function Summary({ envelope }) {
  const b = envelope?.block || {};
  const v = vocab(envelope);
  const ea = b.execution_account;
  const rows = [
    ["Status", labelOf(v.statuses, b.status)],
    ["Block", `${labelOf(v.sides, b.side)} ${b.total_quantity} sh ${b.security?.name || ""}${b.security?.ticker ? ` (${b.security.ticker})` : ""}`],
    ["Executed by", `${labelOf(v.executor_types, b.executor_type)}${b.executor_name ? ` — ${b.executor_name}` : ""}`],
    ["Executing account", ea ? `${ea.broker_name} ${ea.account_reference_masked}` : "—"],
    ["Route", `${labelOf(v.routes, b.route)}${b.manual_reason ? ` — ${b.manual_reason}` : ""}`],
    ["Price guidance", guidanceText(b, v)],
    ["Execute by", b.execute_by],
    ["If not filled", labelOf(v.if_not_filled, b.if_not_filled)],
    ["Filled", `${b.filled_quantity || "0"} of ${b.total_quantity}`],
    ["Average price", b.average_price ? `${b.average_price} (${v.price_rounding || ""})` : "—"],
    ["Policy", b.policy ? `version ${b.policy.version_no} — minimum ${b.policy.minimum_allocation}` : "—"],
    ["Remainder", b.remainder_handling || "—"],
    ["Cross-trade", b.cross_flag ? `flagged — ${b.cross_reason}` : "none"],
  ];
  return (
    <div className={`${card} p-6`}>
      <dl className="grid grid-cols-1 gap-x-6 gap-y-2 text-sm md:grid-cols-2">
        {rows.map(([k, val]) => (
          <div key={k} className="flex gap-3"><dt className="w-36 shrink-0 text-text-muted">{k}</dt><dd>{val ?? "—"}</dd></div>
        ))}
      </dl>
    </div>
  );
}

export function SettlementForm({ envelope, allocation, onDone }) {
  const b = envelope?.block || {};
  const v = vocab(envelope);
  const c = blockControls(envelope);
  const [f, setF] = useState({ status: "affirmed", note: "", failReason: "", reference: "" });
  const [msg, setMsg] = useState(null);
  if (!c.settlement || ["settled", "cancelled"].includes(allocation.settlement_status)) return null;
  async function save() {
    setMsg(null);
    try {
      await post(`blocks/${b.id}/allocations/${allocation.id}/settlement`, {
        status: f.status, note: f.note || undefined, fail_reason: f.failReason || undefined,
        method: f.status === "settled" ? (f.reference ? "custodian_data" : "manual") : undefined,
        reference: f.reference || undefined,
      });
      onDone?.();
    } catch (err) { setMsg(err.message); }
  }
  return (
    <span className="flex flex-wrap items-center gap-1">
      <select className={input} value={f.status} onChange={(e) => setF({ ...f, status: e.target.value })}>
        {(v.settlement_statuses || []).filter((s) => ["pending", "affirmed", "settled", "failed"].includes(s.key))
          .map((s) => <option key={s.key} value={s.key}>{s.label}</option>)}
      </select>
      {f.status === "failed" ? <input className={input} placeholder="Reason" value={f.failReason} onChange={(e) => setF({ ...f, failReason: e.target.value })} /> : null}
      {f.status === "settled" ? <input className={input} placeholder="Custodian reference (or a note)" value={f.reference} onChange={(e) => setF({ ...f, reference: e.target.value })} /> : null}
      <input className={input} placeholder="Note" value={f.note} onChange={(e) => setF({ ...f, note: e.target.value })} />
      <button type="button" className={secondary} onClick={save}>Record settlement</button>
      {msg ? <span className="text-[#9B2335]">{msg}</span> : null}
    </span>
  );
}

export function Allocations({ envelope, onDone }) {
  const b = envelope?.block || {};
  const v = vocab(envelope);
  const allocs = Array.isArray(b.allocations) ? b.allocations : [];
  return (
    <div className={`${card} overflow-x-auto p-6`}>
      <h2 className="text-lg font-semibold text-navy">Allocation</h2>
      <table className="mt-3 w-full text-sm">
        <thead className="text-left text-text-muted"><tr><th className="py-1">Account</th><th>Requested</th><th>Fixed</th><th>Booked</th><th>Settlement</th><th>Status</th><th /></tr></thead>
        <tbody>{allocs.map((a) => (
          <tr key={a.id} className="align-top">
            <td className="py-1">{a.account_number_masked}{a.fractional ? " (fractional)" : ""}</td>
            <td>{a.requested_quantity}</td>
            <td>{a.allocated_quantity}</td>
            <td>{a.final_quantity ?? "—"}{a.zero_reason ? <span className="block text-text-muted">{a.zero_reason}</span> : null}</td>
            <td>{settlementText(a)}</td>
            <td>{labelOf(v.allocation_statuses, a.status)}{a.settlement_status ? ` · ${labelOf(v.settlement_statuses, a.settlement_status)}` : ""}{a.fail_reason ? <span className="block text-[#9B2335]">{a.fail_reason}</span> : null}</td>
            <td><SettlementForm envelope={envelope} allocation={a} onDone={onDone} /></td>
          </tr>
        ))}</tbody>
      </table>
    </div>
  );
}

export function TaskPanel({ envelope, onDone }) {
  const b = envelope?.block || {};
  const c = blockControls(envelope);
  const [note, setNote] = useState("");
  const [msg, setMsg] = useState(null);
  const tasks = Array.isArray(b.tasks) ? b.tasks : [];
  const open = tasks.find((t) => t.status === "open");
  async function decide(outcome) {
    setMsg(null);
    try { await post(`blocks/${b.id}/${outcome}`, { note: note || undefined }); onDone?.(); } catch (err) { setMsg(err.message); }
  }
  return (
    <div className={`${card} p-6 text-sm`}>
      <h2 className="text-lg font-semibold text-navy">Approval</h2>
      {open ? <p className="mt-2">Awaiting a decision by a holder of {open.eligible_permission} — a different person from whoever built it. Approving fixes the allocation.</p>
        : <p className="mt-2 text-text-muted">No approval is open.</p>}
      {c.approve || c.reject ? (
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <input className={input} placeholder="Note (required to reject)" value={note} onChange={(e) => setNote(e.target.value)} />
          {c.approve ? <button type="button" className={primary} onClick={() => decide("approve")}>Approve</button> : null}
          {c.reject ? <button type="button" className={secondary} onClick={() => decide("reject")}>Reject</button> : null}
        </div>
      ) : null}
      {msg ? <p className="mt-2 text-[#9B2335]">{msg}</p> : null}
    </div>
  );
}

export function ActionBar({ envelope, onDone }) {
  const b = envelope?.block || {};
  const v = vocab(envelope);
  const c = blockControls(envelope);
  const [mode, setMode] = useState(null);
  const [f, setF] = useState({ channel: v.sent_channels?.[0]?.key || "", reason: "", quantity: "", price: "", filledAt: "", venue: "" });
  const [msg, setMsg] = useState(null);
  async function run(path, body) {
    setMsg(null);
    try { await post(`blocks/${b.id}/${path}`, body); setMode(null); onDone?.(); } catch (err) { setMsg(err.message); }
  }
  if (!(c.submit || c.markSent || c.fill || c.allocate || c.close || c.cancel)) return null;
  return (
    <div className={`${card} p-4 text-sm`}>
      <div className="flex flex-wrap gap-2">
        {c.submit ? <button type="button" className={primary} onClick={() => run("submit", {})}>Submit for approval</button> : null}
        {c.markSent ? <button type="button" className={primary} onClick={() => setMode("sent")}>Mark sent</button> : null}
        {c.fill ? <button type="button" className={primary} onClick={() => setMode("fill")}>Enter fill</button> : null}
        {c.allocate ? <button type="button" className={primary} onClick={() => run("allocate", {})}>Allocate at the average price</button> : null}
        {c.close ? <button type="button" className={primary} onClick={() => run("close", {})}>Close block</button> : null}
        {c.cancel ? <button type="button" className={secondary} onClick={() => setMode("cancel")}>Cancel block</button> : null}
      </div>
      {mode === "sent" ? (
        <div className="mt-3 flex flex-wrap items-end gap-2">
          <select className={input} value={f.channel} onChange={(e) => setF({ ...f, channel: e.target.value })}>
            {(v.sent_channels || []).map((s) => <option key={s.key} value={s.key}>{s.label}</option>)}
          </select>
          <button type="button" className={primary} onClick={() => run("sent", { channel: f.channel })}>Confirm sent</button>
        </div>
      ) : null}
      {mode === "fill" ? (
        <div className="mt-3 flex flex-wrap items-end gap-2">
          <input className={input} inputMode="decimal" placeholder="Quantity" value={f.quantity} onChange={(e) => setF({ ...f, quantity: e.target.value })} />
          <input className={input} inputMode="decimal" placeholder="Price" value={f.price} onChange={(e) => setF({ ...f, price: e.target.value })} />
          <input type="datetime-local" className={input} value={f.filledAt} onChange={(e) => setF({ ...f, filledAt: e.target.value })} />
          <input className={input} placeholder="Venue" value={f.venue} onChange={(e) => setF({ ...f, venue: e.target.value })} />
          <button type="button" className={primary} onClick={() => run("fills", { quantity: f.quantity.trim(), price: f.price.trim(), filled_at: f.filledAt ? new Date(f.filledAt).toISOString() : null, venue: f.venue || undefined })}>Save fill</button>
        </div>
      ) : null}
      {mode === "cancel" ? (
        <div className="mt-3 flex flex-wrap items-end gap-2">
          <input className={input} placeholder="Reason (required)" value={f.reason} onChange={(e) => setF({ ...f, reason: e.target.value })} />
          <button type="button" className={secondary} onClick={() => run("cancel", { reason: f.reason })}>Confirm cancel</button>
        </div>
      ) : null}
      {msg ? <p className="mt-2 text-[#9B2335]">{msg}</p> : null}
    </div>
  );
}

export function Fills({ envelope }) {
  const fills = Array.isArray(envelope?.block?.fills) ? envelope.block.fills : [];
  return (
    <div className={`${card} p-6 text-sm`}>
      <h2 className="text-lg font-semibold text-navy">Fills</h2>
      {fills.length ? (
        <table className="mt-3 w-full">
          <thead className="text-left text-text-muted"><tr><th className="py-1">Time</th><th>Quantity</th><th>Price</th><th>Venue</th><th>Entered by</th></tr></thead>
          <tbody>{fills.map((x) => (
            <tr key={x.id}><td className="py-1">{String(x.filled_at).slice(0, 16).replace("T", " ")}</td><td>{x.quantity}</td><td>{x.price}</td><td>{x.venue || "—"}</td><td>{x.entered_by_name}</td></tr>
          ))}</tbody>
        </table>
      ) : <p className="mt-2 text-text-muted">No fills entered.</p>}
    </div>
  );
}

export function Corrections({ envelope, onDone }) {
  const b = envelope?.block || {};
  const v = vocab(envelope);
  const c = blockControls(envelope);
  const items = Array.isArray(b.corrections) ? b.corrections : [];
  const allocs = (Array.isArray(b.allocations) ? b.allocations : []).filter((a) => a.status === "allocated");
  const [q, setQ] = useState({});
  const [reason, setReason] = useState("");
  const [note, setNote] = useState("");
  const [msg, setMsg] = useState(null);
  async function ask() {
    setMsg(null);
    const changes = Object.entries(q).filter(([, x]) => String(x).trim()).map(([id, x]) => ({ allocation_id: id, quantity: String(x).trim() }));
    try { await post(`blocks/${b.id}/corrections`, { changes, reason }); onDone?.(); } catch (err) { setMsg(err.message); }
  }
  async function decide(id, outcome) {
    setMsg(null);
    try { await post(`blocks/corrections/${id}/${outcome}`, { note: note || undefined }); onDone?.(); } catch (err) { setMsg(err.message); }
  }
  if (!items.length && !c.correct) return null;
  return (
    <div className={`${card} p-6 text-sm`}>
      <h2 className="text-lg font-semibold text-navy">Corrections</h2>
      <p className="mt-1 text-text-muted">A booked allocation changes only through a correction with a reason, approved by a second person. The original stays on record.</p>
      {items.map((x) => (
        <div key={x.id} className="mt-2">
          <p>{labelOf(v.correction_statuses, x.status)} — {x.reason}</p>
          <ul className="text-text-muted">{(x.changes || []).map((ch) => <li key={ch.allocation_id}>{ch.account}: {ch.before} → {ch.after}</li>)}</ul>
          {x.status === "draft" && c.decideCorrection ? (
            <div className="mt-1 flex flex-wrap gap-2">
              <input className={input} placeholder="Note (required to reject)" value={note} onChange={(e) => setNote(e.target.value)} />
              <button type="button" className={primary} onClick={() => decide(x.id, "approve")}>Approve correction</button>
              <button type="button" className={secondary} onClick={() => decide(x.id, "reject")}>Reject correction</button>
            </div>
          ) : null}
        </div>
      ))}
      {c.correct ? (
        <div className="mt-3 flex flex-wrap items-end gap-2">
          {allocs.map((a) => (
            <label key={a.id} className="flex flex-col gap-1">{a.account_number_masked} (now {a.final_quantity})
              <input className={`${input} w-28`} inputMode="decimal" value={q[a.id] || ""} onChange={(e) => setQ({ ...q, [a.id]: e.target.value })} />
            </label>
          ))}
          <input className={input} placeholder="Reason (required)" value={reason} onChange={(e) => setReason(e.target.value)} />
          <button type="button" className={secondary} onClick={ask}>Ask for correction</button>
        </div>
      ) : null}
      {msg ? <p className="mt-2 text-[#9B2335]">{msg}</p> : null}
    </div>
  );
}

export function Charges({ envelope, onDone }) {
  const b = envelope?.block || {};
  const v = vocab(envelope);
  const c = blockControls(envelope);
  const items = Array.isArray(b.charges) ? b.charges : [];
  const [f, setF] = useState({ kind: "commission", amount: "", payee: "", tradeAway: false });
  const [msg, setMsg] = useState(null);
  async function add() {
    setMsg(null);
    try { await post(`blocks/${b.id}/charges`, { kind: f.kind, amount: f.amount.trim(), payee: f.payee || undefined, trade_away: f.tradeAway }); onDone?.(); } catch (err) { setMsg(err.message); }
  }
  if (!items.length && !c.charge) return null;
  return (
    <div className={`${card} p-6 text-sm`}>
      <h2 className="text-lg font-semibold text-navy">Commissions and fees</h2>
      <ul className="mt-2">{items.map((x) => (
        <li key={x.id} className={x.valid_to ? "text-text-muted line-through" : ""}>{labelOf(v.charge_kinds, x.kind)} {x.amount} {x.currency_code}{x.payee ? ` — ${x.payee}` : ""}{x.trade_away ? " (trade-away)" : ""}</li>
      ))}</ul>
      {c.charge ? (
        <div className="mt-3 flex flex-wrap items-end gap-2">
          <select className={input} value={f.kind} onChange={(e) => setF({ ...f, kind: e.target.value })}>
            {(v.charge_kinds || []).map((k) => <option key={k.key} value={k.key}>{k.label}</option>)}
          </select>
          <input className={input} inputMode="decimal" placeholder="Amount" value={f.amount} onChange={(e) => setF({ ...f, amount: e.target.value })} />
          <input className={input} placeholder="Payee" value={f.payee} onChange={(e) => setF({ ...f, payee: e.target.value })} />
          <label className="flex items-center gap-2"><input type="checkbox" checked={f.tradeAway} onChange={(e) => setF({ ...f, tradeAway: e.target.checked })} />Trade-away</label>
          <button type="button" className={secondary} onClick={add}>Add charge</button>
        </div>
      ) : null}
      {msg ? <p className="mt-2 text-[#9B2335]">{msg}</p> : null}
    </div>
  );
}

export function SheetPanel({ envelope }) {
  const b = envelope?.block || {};
  const c = blockControls(envelope);
  const [msg, setMsg] = useState(null);
  if (!c.sheet) return null;
  async function download() {
    setMsg(null);
    const res = await fetch(`/api/execution/blocks/${b.id}/sheet`, { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" });
    if (!res.ok) { const data = await res.json().catch(() => ({})); setMsg(data.error || "Could not generate the sheet"); return; }
    const url = URL.createObjectURL(await res.blob());
    const a = document.createElement("a");
    a.href = url; a.download = `order-execution-sheet-${String(b.id).slice(0, 8)}.pdf`; a.click();
    URL.revokeObjectURL(url);
  }
  return (
    <div className={`${card} p-6 text-sm`}>
      <h2 className="text-lg font-semibold text-navy">Order execution sheet</h2>
      <p className="mt-1 text-text-muted">Print it and give it to the broker of your choice; enter the fills by hand. Account references are masked and no member names appear. Nothing is sent electronically.</p>
      <div className="mt-3"><button type="button" className={primary} onClick={download}>Download order execution sheet</button></div>
      {msg ? <p className="mt-2 text-[#9B2335]">{msg}</p> : null}
    </div>
  );
}

export function Timeline({ envelope }) {
  const b = envelope?.block || {};
  const v = vocab(envelope);
  const items = Array.isArray(b.timeline) ? b.timeline : [];
  return (
    <div className={`${card} p-6`}>
      <h2 className="text-lg font-semibold text-navy">Timeline</h2>
      <ol className="mt-3 space-y-2 text-sm">{items.map((e) => (
        <li key={e.id} className="flex gap-3">
          <span className="w-40 shrink-0 text-text-muted">{String(e.at).slice(0, 16).replace("T", " ")}</span>
          <span><span className="font-medium text-navy">{labelOf(v.actions, e.action)}</span>
            {e.from_status !== e.to_status ? ` — ${labelOf(v.statuses, e.to_status)}` : ""}
            <span className="text-text-muted"> · {e.actor_name}</span>
            {e.detail?.reason || e.detail?.note ? <span className="block text-text-muted">{e.detail.reason || e.detail.note}</span> : null}
          </span>
        </li>
      ))}</ol>
    </div>
  );
}

export default function BlockDetail({ initialEnvelope }) {
  const [env, setEnv] = useState(initialEnvelope);
  const id = env?.block?.id;
  async function reload() {
    const res = await fetch(`/api/execution/blocks/${id}`);
    const data = await res.json().catch(() => ({}));
    if (res.ok) setEnv(data);
  }
  return (
    <div className="space-y-5">
      <Summary envelope={env} />
      <ActionBar envelope={env} onDone={reload} />
      <TaskPanel envelope={env} onDone={reload} />
      <Allocations envelope={env} onDone={reload} />
      <Fills envelope={env} />
      <Corrections envelope={env} onDone={reload} />
      <Charges envelope={env} onDone={reload} />
      <SheetPanel envelope={env} />
      <Timeline envelope={env} />
    </div>
  );
}
