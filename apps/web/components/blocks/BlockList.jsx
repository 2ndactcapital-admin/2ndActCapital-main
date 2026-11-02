"use client";

/**
 * BlockList — Portfolio → Blocks (REP9).
 *
 * The firm's trade blocks, and the builder: approved account instructions the
 * server says are eligible (with suggested groupings by security / side /
 * executor / execute-by), the ineligible ones with the server's plain reason,
 * an allocation preview under the approved policy, and Build. Every write
 * control renders only from `listControls` / `builderControls` (the
 * envelope's own flags — fail closed). Labels are the envelope's.
 */

import { useState } from "react";
import { buildBody, builderControls, guidanceText, labelOf, listControls, vocab } from "@/lib/blocks.mjs";

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

export function BlockTable({ envelope }) {
  const rows = Array.isArray(envelope?.rows) ? envelope.rows : [];
  const v = vocab(envelope);
  if (!rows.length) {
    return <div className={`${card} p-10 text-center text-sm text-text-muted`}>No blocks yet.</div>;
  }
  return (
    <div className={`${card} overflow-x-auto`}>
      <table className="w-full text-sm">
        <thead className="border-b border-[#ece8dd] text-left text-text-muted">
          <tr>
            <th className="px-4 py-3 font-medium">Block</th>
            <th className="px-4 py-3 font-medium">Accounts</th>
            <th className="px-4 py-3 font-medium">Executor</th>
            <th className="px-4 py-3 font-medium">Execute by</th>
            <th className="px-4 py-3 font-medium">Average price</th>
            <th className="px-4 py-3 font-medium">Status</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id} className="border-b border-[#ece8dd] last:border-0">
              <td className="px-4 py-3">
                <a className="text-navy underline" href={`/portfolio/blocks/${r.id}`}>
                  {labelOf(v.sides, r.side)} {r.total_quantity} sh {r.security_name}
                </a>
              </td>
              <td className="px-4 py-3">{r.accounts}</td>
              <td className="px-4 py-3">{labelOf(v.executor_types, r.executor_type)}{r.executor_name ? ` — ${r.executor_name}` : ""}</td>
              <td className="px-4 py-3">{r.execute_by}</td>
              <td className="px-4 py-3">{r.average_price || "—"}</td>
              <td className="px-4 py-3">{labelOf(v.statuses, r.status)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function ComplianceNotes({ envelope }) {
  const notes = Array.isArray(vocab(envelope).compliance_notes) ? vocab(envelope).compliance_notes : [];
  if (!notes.length) return null;
  return (
    <div className={`${card} p-6 text-sm`}>
      <h2 className="text-lg font-semibold text-navy">Open for the firm and counsel</h2>
      <ul className="mt-2 list-disc space-y-1 pl-5 text-text-muted">{notes.map((n) => <li key={n}>{n}</li>)}</ul>
    </div>
  );
}

export function Builder({ candidates }) {
  const c = builderControls(candidates);
  const v = vocab(candidates);
  const eligible = Array.isArray(candidates?.eligible) ? candidates.eligible : [];
  const ineligible = Array.isArray(candidates?.ineligible) ? candidates.ineligible : [];
  const suggestions = Array.isArray(candidates?.suggestions) ? candidates.suggestions : [];
  const accounts = Array.isArray(candidates?.execution_accounts) ? candidates.execution_accounts : [];
  const [sel, setSel] = useState([]);
  const [form, setForm] = useState({ priceKind: "market", ifNotFilled: "ask" });
  const [pv, setPv] = useState(null);
  const [msg, setMsg] = useState(null);
  const toggle = (id) => setSel(sel.includes(id) ? sel.filter((x) => x !== id) : [...sel, id]);
  async function preview() {
    setMsg(null);
    try { setPv((await post("blocks/preview", { instruction_ids: sel })).preview); } catch (err) { setMsg(err.message); }
  }
  async function build() {
    setMsg(null);
    try {
      const out = await post("blocks", buildBody({ ...form, instructionIds: sel }));
      window.location.href = `/portfolio/blocks/${out.block.id}`;
    } catch (err) { setMsg(err.message); }
  }
  return (
    <div className={`${card} space-y-4 p-6 text-sm`}>
      <h2 className="text-lg font-semibold text-navy">Build a block</h2>
      {candidates?.policy ? (
        <p className="text-text-muted">Allocated under policy version {candidates.policy.version_no} ({labelOf(v.methods, candidates.policy.method)}; minimum {candidates.policy.minimum_allocation}). The allocation is fixed when a second person approves the block.</p>
      ) : (
        <p className="text-[#9B2335]">No approved allocation policy yet — the firm writes it in Settings → Allocation policy before any block is built.</p>
      )}
      {suggestions.length ? (
        <div>
          <p className="font-medium text-navy">Suggested groupings</p>
          <ul className="mt-1 space-y-1">{suggestions.map((s) => (
            <li key={`${s.global_security_id}-${s.side}-${s.executor_type}-${s.execute_by}`}>
              <button type="button" className="text-navy underline" onClick={() => setSel(s.instruction_ids)}>
                {labelOf(v.sides, s.side)} {s.total} sh {s.security_name} · {labelOf(v.executor_types, s.executor_type)} · by {s.execute_by} · {s.accounts} accounts
              </button>
            </li>
          ))}</ul>
        </div>
      ) : null}
      <table className="w-full">
        <thead className="text-left text-text-muted"><tr><th className="py-1" /><th>Account</th><th>Trade</th><th>Executor</th><th>By</th><th>Settles</th></tr></thead>
        <tbody>{eligible.map((e) => (
          <tr key={e.instruction_id}>
            <td className="py-1"><input type="checkbox" checked={sel.includes(e.instruction_id)} onChange={() => toggle(e.instruction_id)} /></td>
            <td>{e.account_number_masked}{e.fractional ? " (fractional)" : ""}</td>
            <td>{labelOf(v.sides, e.side)} {e.remaining} sh {e.security_name}</td>
            <td>{labelOf(v.executor_types, e.executor_type)}</td>
            <td>{e.execute_by}</td>
            <td>{labelOf(v.settlement_kinds, e.settlement_kind)}</td>
          </tr>
        ))}</tbody>
      </table>
      {ineligible.length ? (
        <div>
          <p className="font-medium text-navy">Not eligible</p>
          <ul className="mt-1 space-y-1 text-text-muted">{ineligible.map((e) => (
            <li key={e.instruction_id}>{e.account_number_masked} · {labelOf(v.sides, e.side)} {e.quantity} sh {e.security_name}: {e.reason}</li>
          ))}</ul>
        </div>
      ) : null}
      {c.preview ? <button type="button" className={secondary} disabled={!sel.length} onClick={preview}>Preview allocation</button> : null}
      {pv ? (
        <div className="rounded border border-[#ece8dd] p-4">
          <p className="font-medium text-navy">Allocation preview — {pv.total_quantity} sh</p>
          {pv.reference ? <p className="text-text-muted">{guidanceText({ price_kind: "market", reference: pv.reference }, v)}</p> : null}
          {Array.isArray(pv.crosses) && pv.crosses.length ? <p className="text-[#9B2335]">Cross-trade risk: an opposite block of this security exists in the same window — a reason is required.</p> : null}
          <ul className="mt-1">{(pv.allocations || []).map((a) => <li key={a.instruction_id}>{a.account_number_masked}: {a.allocated_quantity}</li>)}</ul>
          {(pv.refused || []).map((r) => <p key={r.instruction_id} className="text-[#9B2335]">{r.account_number_masked}: {r.reason}</p>)}
        </div>
      ) : null}
      {c.build ? (
        <div className="flex flex-wrap items-end gap-2">
          <select className={input} value={form.executionAccountId || ""} onChange={(e) => setForm({ ...form, executionAccountId: e.target.value })}>
            <option value="">Executing account (broker / trader blocks)</option>
            {accounts.map((a) => <option key={a.id} value={a.id}>{a.broker_name} {a.account_reference_masked}{a.dvp_capable ? "" : " (not DVP)"}</option>)}
          </select>
          <input className={input} placeholder="Broker / trader name" value={form.executorName || ""} onChange={(e) => setForm({ ...form, executorName: e.target.value })} />
          <select className={input} value={form.priceKind} onChange={(e) => setForm({ ...form, priceKind: e.target.value })}>
            {(v.price_kinds || []).map((p) => <option key={p.key} value={p.key}>{p.label}</option>)}
          </select>
          {form.priceKind === "limit" ? <input className={input} inputMode="decimal" placeholder="Limit" value={form.limitPrice || ""} onChange={(e) => setForm({ ...form, limitPrice: e.target.value })} /> : null}
          {form.priceKind === "range" ? (<>
            <input className={input} inputMode="decimal" placeholder="Low" value={form.rangeLow || ""} onChange={(e) => setForm({ ...form, rangeLow: e.target.value })} />
            <input className={input} inputMode="decimal" placeholder="High" value={form.rangeHigh || ""} onChange={(e) => setForm({ ...form, rangeHigh: e.target.value })} />
          </>) : null}
          <input className={input} inputMode="decimal" placeholder="Reference price (optional)" value={form.referencePrice || ""} onChange={(e) => setForm({ ...form, referencePrice: e.target.value })} />
          <select className={input} value={form.ifNotFilled} onChange={(e) => setForm({ ...form, ifNotFilled: e.target.value })}>
            {(v.if_not_filled || []).map((p) => <option key={p.key} value={p.key}>{p.label}</option>)}
          </select>
          <input className={input} placeholder="Firm contact" value={form.firmContact || ""} onChange={(e) => setForm({ ...form, firmContact: e.target.value })} />
          <input className={input} placeholder="Cross-trade reason (if flagged)" value={form.crossReason || ""} onChange={(e) => setForm({ ...form, crossReason: e.target.value })} />
          <button type="button" className={primary} disabled={!sel.length} onClick={build}>Build block</button>
          <span className="w-full text-text-muted">Route: {labelOf(v.routes, "manual")}. {labelOf(v.routes, "fix")}.</span>
        </div>
      ) : null}
      {msg ? <p className="text-[#9B2335]">{msg}</p> : null}
    </div>
  );
}

export default function BlockList({ initialEnvelope, candidates }) {
  const c = listControls(initialEnvelope);
  const [open, setOpen] = useState(false);
  return (
    <div className="space-y-5">
      {c.build ? (
        <div><button type="button" className={primary} onClick={() => setOpen(!open)}>New block</button></div>
      ) : null}
      {c.build && open && candidates ? <Builder candidates={candidates} /> : null}
      <BlockTable envelope={initialEnvelope} />
      <ComplianceNotes envelope={initialEnvelope} />
    </div>
  );
}
