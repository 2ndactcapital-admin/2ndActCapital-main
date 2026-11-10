"use client";

/**
 * TreasuryInvoices — Household → Treasury → Invoices (CASH08), STAFF only, inside the EXISTING Treasury tab: the
 * household's Chancery invoices awaiting recording, the record form (an active payee, the paying member, the invoice
 * number, the amount and due date — typed by a person; nothing is extracted by a model), and the invoices already
 * recorded (each a FIRM vendor-invoice obligation, so it is in the coverage above at once). A second record of the same
 * payee + invoice number + amount is refused by the server. Recording an invoice pays nothing. Every decision comes from
 * lib/cashPayees.mjs, which fails closed. Reads go through the Next.js proxy (/api/cash/households/{id}/invoices…).
 * Light / cream.
 *
 * ``initial`` ({list}) renders without fetching (render tests).
 */

import { useCallback, useEffect, useState } from "react";
import { canRecord, invoiceRows, readable, recordBody, recordChoices } from "@/lib/cashPayees.mjs";

const card = "rounded-[6px] border border-[#ece8dd] bg-white p-4";
const serif = { fontFamily: "'Spectral', Georgia, serif" };
const input = "rounded border border-[#E2E8F0] px-2 py-1 text-sm";
const btn = "rounded border border-[#C5A880] bg-[#F5F1EB] px-3 py-1 text-sm text-navy";

async function call(path, { method = "GET", body } = {}) {
  const res = await fetch(path, { method, headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail?.message || data.error || data.detail || `Request failed (${res.status})`);
  return data;
}

export function InvoiceList({ data, onRecord }) {
  if (!readable(data)) return null;
  const { awaiting, recorded } = invoiceRows(data);
  const can = canRecord(data);
  return (
    <section id="treasury-invoices" className="space-y-2" aria-label="Invoices">
      <h3 className="text-lg font-semibold text-navy" style={serif}>Invoices</h3>
      <h4 className="text-sm font-semibold text-[#334155]">Awaiting recording</h4>
      {awaiting.length === 0 ? <p className={`${card} text-sm text-text-muted`} data-empty="awaiting">No invoices awaiting recording.</p> : (
        <ul className={`${card} space-y-1 text-sm`}>{awaiting.map((d) => (
          <li key={d.id} className="flex items-baseline gap-2" data-awaiting={d.id}>
            <span className="mr-auto">{d.name}</span>
            {can ? <button type="button" className={btn} onClick={() => onRecord?.(d.id)}>Record invoice</button> : null}
          </li>))}</ul>)}
      <h4 className="text-sm font-semibold text-[#334155]">Recorded</h4>
      {recorded.length === 0 ? <p className={`${card} text-sm text-text-muted`} data-empty="recorded">None recorded.</p> : (
        <table className={`${card} w-full text-sm`}>
          <thead className="text-left text-text-muted"><tr><th className="py-1">Obligation</th><th>Payee</th><th>Amount</th><th>Due</th></tr></thead>
          <tbody>{recorded.map((o) => (
            <tr key={o.id} className="border-t border-[#ece8dd]" data-recorded={o.id}>
              <td className="py-1">{o.name}</td><td>{o.payee}</td><td>{o.amount}</td><td>{o.due}</td></tr>))}
          </tbody>
        </table>)}
    </section>
  );
}

export function RecordInvoiceForm({ data, documentId, onSave, onCancel, busy = false }) {
  const [form, setForm] = useState({ payee_id: "", entity_id: "", invoice_number: "", amount: "", due_date: "", notes: "" });
  const [err, setErr] = useState(null);
  if (!canRecord(data) || !documentId) return null;
  const ch = recordChoices(data);
  const set = (k) => (e) => setForm((c) => ({ ...c, [k]: e.target.value }));
  const go = () => {
    const b = recordBody(data, { ...form, document_id: documentId });
    if (!b.ok) { setErr(b.error); return; }
    setErr(null);
    onSave?.(b.body);
  };
  return (
    <section className={`${card} space-y-2`} aria-label="Record invoice">
      <h4 className="font-semibold text-navy">Record invoice — a firm obligation of the household</h4>
      <label className="block text-sm">Payee
        <select className={`${input} ml-2`} aria-label="Payee" value={form.payee_id} onChange={set("payee_id")}>
          <option value="">Choose an active payee</option>
          {ch.payees.map((p) => <option key={p.key} value={p.key}>{p.label}</option>)}
        </select></label>
      <label className="block text-sm">Paid by
        <select className={`${input} ml-2`} aria-label="Paid by" value={form.entity_id} onChange={set("entity_id")}>
          <option value="">Choose a household member</option>
          {ch.payers.map((p) => <option key={p.key} value={p.key}>{p.label}</option>)}
        </select></label>
      {[["invoice_number", "Invoice number", "text"], ["amount", "Amount", "text"], ["due_date", "Due date", "date"],
        ["notes", "Notes", "text"]].map(([k, label, type]) => (
        <label key={k} className="block text-sm">{label}
          <input className={`${input} ml-2`} aria-label={label} type={type} value={form[k]} onChange={set(k)} /></label>))}
      <p className="text-xs text-text-muted">Typed from the invoice — nothing is read from the document automatically.</p>
      {err ? <p className="text-sm text-[#9B2335]" role="alert">{err}</p> : null}
      <div className="flex gap-2">
        <button type="button" className={btn} disabled={busy} onClick={go}>Record</button>
        <button type="button" className={btn} onClick={onCancel}>Cancel</button>
      </div>
    </section>
  );
}

export default function TreasuryInvoices({ householdId, initial = null }) {
  const [list, setList] = useState(initial?.list || null);
  const [doc, setDoc] = useState(initial?.documentId || null);
  const [err, setErr] = useState(null);
  const [busy, setBusy] = useState(false);
  const base = householdId ? `/api/cash/households/${encodeURIComponent(householdId)}/invoices` : null;
  const reload = useCallback(() => {
    if (!base) return;
    // a member's read is refused by the server (staff only): nothing renders
    call(base).then(setList).catch(() => setList(null));
  }, [base]);
  useEffect(() => { if (!initial) reload(); }, [initial, reload]);
  if (!readable(list)) return null;
  const save = async (body) => {
    setBusy(true);
    try { await call(`${base}/record`, { method: "POST", body }); setDoc(null); setErr(null); reload(); }
    catch (e) { setErr(e.message); } finally { setBusy(false); }
  };
  return (
    <div className="space-y-3">
      <InvoiceList data={list} onRecord={setDoc} />
      <RecordInvoiceForm data={list} documentId={doc} busy={busy} onSave={save} onCancel={() => setDoc(null)} />
      {err ? <p className="text-sm text-[#9B2335]" role="alert">{err}</p> : null}
    </div>
  );
}
