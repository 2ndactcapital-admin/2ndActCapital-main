"use client";

/**
 * TreasuryPayments — Household → Treasury → Payments (CASH07): payment instructions between a family's OWN accounts,
 * inside the EXISTING Treasury tab (no second tab). The list with its filters, the new-instruction form (source /
 * destination limited to same-owner accounts, rails limited to what the custodian declares, a business-day value date,
 * an optional link to an obligation or a cash proposal), the detail with the lifecycle TIMELINE (payment_events), the
 * printable payment sheet, reconcile (matched feed rows, or manual with a reason) and cancel. Approve / Return to draft
 * appear only when the server lists them on the row (the S30 floor — a maker never sees them); the approval task is
 * also in the existing inbox. A member reads their family's payments read-only (last four only) with the
 * cash.payments disclosure. Every decision comes from lib/cashPayments.mjs, which fails closed. Each read goes through
 * the Next.js proxy (/api/cash/households/{id}/payments…) — never FastAPI from the browser. Light / cream.
 *
 * CPSIM04: for a payment whose source is at a SIMULATED custodian, the custodian panel — "Send to custodian
 * (simulated)" (only when the server lists it and its D4 route holds; otherwise the reason it is manual), the transfer's
 * custodian events, "Refresh status from custodian", and the reconcile picker preselecting the transfer's matched feed
 * rows — every one with a SIMULATED badge. The manual route (sheet + Confirm sent) stays offered beside it.
 *
 * ``initial`` ({list, options, detail, matches, transfer}) renders without fetching (render tests).
 */

import { useCallback, useEffect, useState } from "react";
import {
  accountText, actionRequest, canWrite, CUSTODIAN_PATHS, custodianPanel, destinationChoices, detailOf, disclosureText,
  draftBody, filterOptions, isMember, labelOf, linkChoices, newOffer, paymentRows, preselected, railChoices, readable,
  reconcileBody, sourceChoices, transferEvents, vocab,
} from "@/lib/cashPayments.mjs";

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

async function downloadSheet(path) {
  const res = await fetch(path, { method: "POST" });
  if (!res.ok) {
    const d = await res.json().catch(() => ({}));
    throw new Error(d.error || `Request failed (${res.status})`);
  }
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "payment-sheet.pdf";
  a.click();
  URL.revokeObjectURL(url);
}

export function PaymentList({ data, filters, onFilters, onOpen, onNew }) {
  if (!readable(data)) return null;
  const rows = paymentRows(data);
  const f = filterOptions(data);
  const disc = disclosureText(data);
  return (
    <section id="treasury-payments" className="space-y-2" aria-label="Payments">
      <div className="flex flex-wrap items-baseline gap-2">
        <h3 className="mr-auto text-lg font-semibold text-navy" style={serif}>Payments between your accounts</h3>
        {isMember(data) ? null : (
          <>
            <select className={input} aria-label="Status" value={filters?.status || ""}
              onChange={(e) => onFilters?.({ ...filters, status: e.target.value })}>
              <option value="">Every status</option>
              {f.statuses.map((s) => <option key={s.key} value={s.key}>{s.label}</option>)}
            </select>
            <select className={input} aria-label="Rail" value={filters?.rail || ""}
              onChange={(e) => onFilters?.({ ...filters, rail: e.target.value })}>
              <option value="">Every rail</option>
              {f.rails.map((r) => <option key={r.key} value={r.key}>{r.label}</option>)}
            </select>
          </>)}
        {newOffer(data) ? <button type="button" className={btn} onClick={onNew}>New payment instruction</button> : null}
      </div>
      {disc ? <p className="text-xs text-text-muted" data-label="cash.payments">{disc}</p> : null}
      {rows.length === 0 ? <p className={`${card} text-sm text-text-muted`} data-empty="payments">No payment instructions.</p> : (
        <table className={`${card} w-full text-sm`}>
          <thead className="text-left text-text-muted"><tr><th className="py-1">Value date</th><th>Amount</th><th>Rail</th><th>From</th><th>To</th><th>Status</th></tr></thead>
          <tbody>{rows.map((r) => (
            <tr key={r.id} className="cursor-pointer border-t border-[#ece8dd] align-top" onClick={() => onOpen?.(r.id)}>
              <td className="py-1">{r.valueDate}</td><td>{r.amount}</td><td>{r.rail}</td><td>{r.from}</td><td>{r.to}</td>
              <td>{r.statusLabel}{r.provisional ? <span className="block text-xs text-text-muted">{r.provisional}</span> : null}</td>
            </tr>))}
          </tbody>
        </table>)}
    </section>
  );
}

export function NewPaymentForm({ options, onCreate, onCancel, busy = false }) {
  const [form, setForm] = useState({ source: "", destination: "", rail: "", amount: "", valueDate: "", purpose: "", link: "" });
  if (!canWrite(options)) return null;
  const src = sourceChoices(options);
  const dests = destinationChoices(options, form.source);
  const rails = railChoices(options, form.source);
  const links = linkChoices(options);
  const draft = draftBody(options, form);
  const set = (k) => (e) => setForm((cur) => ({ ...cur, [k]: e.target.value, ...(k === "source" ? { destination: "", rail: "" } : {}) }));
  return (
    <section className={`${card} space-y-2`} aria-label="New payment instruction">
      <h4 className="font-semibold text-navy">New payment instruction — between the family&apos;s own accounts</h4>
      <label className="block text-sm">From
        <select className={`${input} ml-2`} aria-label="Source account" value={form.source} onChange={set("source")}>
          <option value="">Choose an account</option>
          {src.eligible.map((s) => <option key={s.key} value={s.key}>{s.label}</option>)}
        </select></label>
      {src.refused.length ? (
        <ul className="text-xs text-text-muted" aria-label="Accounts you cannot instruct">
          {src.refused.map((s) => <li key={s.key} data-refused={s.key}>{s.label}: {s.reasons.join(" · ")}</li>)}
        </ul>) : null}
      <label className="block text-sm">To
        <select className={`${input} ml-2`} aria-label="Destination account" value={form.destination} onChange={set("destination")}>
          <option value="">Choose a same-owner account</option>
          {dests.map((d) => <option key={d.key} value={d.key}>{d.label}</option>)}
        </select></label>
      <label className="block text-sm">Rail
        <select className={`${input} ml-2`} aria-label="Rail" value={form.rail} onChange={set("rail")}>
          <option value="">Choose a rail</option>
          {rails.map((r) => <option key={r.key} value={r.key}>{r.label}</option>)}
        </select></label>
      <label className="block text-sm">Amount (USD)
        <input className={`${input} ml-2`} aria-label="Amount" inputMode="decimal" value={form.amount} onChange={set("amount")} /></label>
      <label className="block text-sm">Value date
        <input className={`${input} ml-2`} type="date" aria-label="Value date" min={options.today} value={form.valueDate} onChange={set("valueDate")} />
        <span className="ml-2 text-xs text-text-muted">{options.value_date_rule} Next business day: {options.next_business_day}.</span></label>
      <label className="block text-sm">Purpose
        <input className={`${input} ml-2 w-2/3`} aria-label="Purpose" value={form.purpose} onChange={set("purpose")} /></label>
      <label className="block text-sm">Linked to (optional)
        <select className={`${input} ml-2`} aria-label="Link" value={form.link} onChange={set("link")}>
          <option value="">Nothing</option>
          {links.map((l) => <option key={l.key} value={l.key}>{l.label}</option>)}
        </select></label>
      {disclosureText(options) ? <p className="text-xs text-text-muted" data-label="cash.payments">{disclosureText(options)}</p> : null}
      {!draft.ok ? <p className="text-xs text-text-muted">{draft.errors.join(" · ")}</p> : null}
      <div className="flex gap-2">
        <button type="button" className={btn} disabled={!draft.ok || busy} onClick={() => draft.ok && onCreate?.(draft.body)}>Save draft</button>
        <button type="button" className="text-sm text-text-muted underline" onClick={onCancel}>Close</button>
      </div>
    </section>
  );
}

export function SimulatedBadge() {
  return <span data-testid="simulated-badge" className="ml-2 rounded border border-[#C5A880] bg-[#F5F1EB] px-1 text-[10px] font-semibold tracking-[0.08em] text-navy">SIMULATED</span>;
}

/** CPSIM04 — the custodian-API panel (only from the server's `payment.custodian`; fails closed). */
export function CustodianPanel({ data, transferView = null, onCustodian, busy = false }) {
  const c = custodianPanel(data);
  if (!c) return null;
  const events = transferEvents(transferView);
  return (
    <div className="rounded-[6px] border border-[#ece8dd] bg-[#FAF9F6] p-2 text-sm" data-testid="custodian-panel">
      <p className="font-semibold text-navy">Custodian API<SimulatedBadge />{c.counterparty ? <span className="ml-2 font-normal text-text-muted">simulated custodian {c.counterparty}</span> : null}</p>
      {c.transferId ? <p className="text-xs text-text-muted" data-testid="transfer-id">Transfer {c.transferId}</p> : null}
      {c.manualReason ? <p className="text-xs text-text-muted" data-testid="manual-reason">{c.manualReason.charAt(0).toUpperCase() + c.manualReason.slice(1)}</p> : null}
      {events.length ? (
        <ol className="text-xs" aria-label="Custodian events">{events.map((e, i) => <li key={i} data-custodian-event={e.status}>{e.status}{e.on ? ` · ${e.on}` : ""}</li>)}</ol>) : null}
      <div className="mt-1 flex flex-wrap gap-2">
        {c.sendOffered ? <button type="button" className={btn} disabled={busy} onClick={() => onCustodian?.("send_to_custodian")}>Send to custodian (simulated)</button> : null}
        {c.refreshOffered ? <button type="button" className={btn} disabled={busy} onClick={() => onCustodian?.("refresh_from_custodian")}>Refresh status from custodian</button> : null}
      </div>
      {c.manualOffered ? <p className="text-xs text-text-muted">The manual route stays available: download the payment sheet and confirm the channel.</p> : null}
    </div>
  );
}

const CUSTODIAN_KEYS = new Set(Object.keys(CUSTODIAN_PATHS));

export function PaymentDetail({ data, matches = null, transferView = null, onAction, onCustodian, onSheet, onMatches,
  onClose, busy = false }) {
  const [fields, setFields] = useState({});
  const [chosen, setChosen] = useState(() => preselected(transferView, matches));
  useEffect(() => {                       // the transfer's matched feed rows, preselected once both reads arrive
    const pre = preselected(transferView, matches);
    if (pre.length) setChosen(pre);
  }, [transferView, matches]);
  const d = detailOf(data);
  if (!d) return null;
  const channels = vocab(data, "channels");
  const set = (k) => (e) => setFields((cur) => ({ ...cur, [k]: e.target.value }));
  const has = (k) => d.actions.some((a) => a.key === k);
  const rec = reconcileBody(matches, chosen, fields.note);
  return (
    <section className={`${card} space-y-2`} aria-label="Payment instruction">
      <div className="flex items-baseline gap-2">
        <h4 className="mr-auto font-semibold text-navy">{d.amount} · {d.rail} · {d.valueDate}</h4>
        <button type="button" className="text-sm text-text-muted underline" onClick={onClose}>Close</button>
      </div>
      <p className="text-sm">From {d.from} → to {d.to}</p>
      <p className="text-sm text-text-muted">{d.purpose}{d.link ? ` · ${d.link}` : ""} · {d.status}
        {d.provisional ? ` · provisional until ${d.provisional} (ACH return window)` : ""}</p>
      {disclosureText(data) ? <p className="text-xs text-text-muted" data-label="cash.payments">{disclosureText(data)}</p> : null}
      <CustodianPanel data={data} transferView={transferView} onCustodian={onCustodian} busy={busy} />
      <ol className="border-l border-[#ece8dd] pl-3 text-sm" aria-label="Timeline">
        {d.timeline.map((e) => (
          <li key={e.id} className="py-0.5" data-event={e.label}><span className="text-text-muted">{(e.at || "").slice(0, 16).replace("T", " ")}</span> — {e.label} ({e.to}) · {e.who}{e.note ? ` · ${e.note}` : ""}</li>))}
      </ol>
      {d.actions.length ? (
        <div className="space-y-2" aria-label="Actions">
          {has("mark_sent") ? (
            <label className="block text-sm">Sent by
              <select className={`${input} ml-2`} aria-label="Channel" value={fields.channel || ""} onChange={set("channel")}>
                <option value="">Choose the channel</option>
                {channels.map((c) => <option key={c.key} value={c.key}>{c.label}</option>)}
              </select></label>) : null}
          {has("settle") ? <label className="block text-sm">Settled on <input className={`${input} ml-2`} type="date" aria-label="Settled on" value={fields.settled_on || ""} onChange={set("settled_on")} /></label> : null}
          {has("return") ? <label className="block text-sm">ACH return code <input className={`${input} ml-2`} aria-label="Return code" value={fields.return_code || ""} onChange={set("return_code")} /></label> : null}
          {has("cancel") || has("custodian_reject") || has("return") ? <label className="block text-sm">Reason <input className={`${input} ml-2 w-2/3`} aria-label="Reason" value={fields.reason || ""} onChange={set("reason")} /></label> : null}
          {has("reconcile") ? (
            <div className="text-sm" aria-label="Reconcile">
              <button type="button" className="text-navy underline" onClick={onMatches}>Find matching feed rows</button>
              {(matches?.matches || []).map((m) => (
                <label key={m.id} className="block"><input type="checkbox" checked={chosen.includes(m.id)}
                  onChange={(e) => setChosen((c) => (e.target.checked ? [...c, m.id] : c.filter((x) => x !== m.id)))} />
                  {" "}{m.date} {m.leg === "out" ? "out of" : "into"} the account · {m.amount} · {m.type}{m.external_ref ? ` · ${m.external_ref}` : ""}
                  {m.source_system === "altruist_simulator" ? <SimulatedBadge /> : null}</label>))}
            </div>) : null}
          <label className="block text-sm">Note <input className={`${input} ml-2 w-2/3`} aria-label="Note" value={fields.note || ""} onChange={set("note")} /></label>
          <div className="flex flex-wrap gap-2">
            {d.actions.filter((a) => !CUSTODIAN_KEYS.has(a.key)).map((a) => (a.key === "sheet"
              ? <button key={a.key} type="button" className={btn} disabled={busy} onClick={onSheet}>{a.label}</button>
              : a.key === "reconcile"
                ? <button key={a.key} type="button" className={btn} disabled={busy || !rec.ok} onClick={() => rec.ok && onAction?.("reconcile", rec.body)}>{a.label}</button>
                : a.key === "edit" ? null
                  : <button key={a.key} type="button" className={btn} disabled={busy} onClick={() => onAction?.(...actionRequest(a.key, fields))}>{a.label}</button>))}
          </div>
        </div>) : null}
    </section>
  );
}

export default function TreasuryPayments({ householdId, initial = null }) {
  const [data, setData] = useState(initial || {});
  const [filters, setFilters] = useState({ status: "", rail: "" });
  const [err, setErr] = useState(null);
  const [busy, setBusy] = useState(false);
  const base = householdId ? `/api/cash/households/${encodeURIComponent(householdId)}/payments` : null;
  const q = new URLSearchParams(Object.entries(filters).filter(([, v]) => v)).toString();

  const loadList = useCallback(() => {
    if (!base || initial) return undefined;
    let alive = true;
    call(`${base}${q ? `?${q}` : ""}`).then((d) => { if (alive) setData((c) => ({ ...c, list: d })); })
      .catch((e) => { if (alive) setErr(e.message); });
    return () => { alive = false; };
  }, [base, q, initial]);
  useEffect(() => loadList(), [loadList]);

  if (!base || !readable(data.list)) return null;
  const open = (id) => call(`${base}/${encodeURIComponent(id)}`).then((d) => setData((c) => ({ ...c, detail: d, matches: null })))
    .catch((e) => setErr(e.message));
  const run = (p) => { setBusy(true); setErr(null); return p.catch((e) => setErr(e.message)).finally(() => setBusy(false)); };
  const pid = data.detail?.payment?.id;
  const act = (key, body) => run(call(`${base}/${encodeURIComponent(pid)}/${key}`, { method: "POST", body })
    .then((d) => { setData((c) => ({ ...c, detail: { ...c.detail, payment: d.payment } })); loadList(); }));
  // cpsim04 — Send / Refresh go to their own routes; the live transfer is re-read after either
  const custodian = (key) => CUSTODIAN_PATHS[key] && run(call(`${base}/${encodeURIComponent(pid)}/${CUSTODIAN_PATHS[key]}`,
    { method: "POST" }).then((d) => {
    setData((c) => ({ ...c, detail: { ...c.detail, payment: d.payment }, transfer: d.transfer ? { transfer: d.transfer } : c.transfer }));
    loadList();
  }));
  return (
    <div className="space-y-3">
      <PaymentList data={data.list} filters={filters} onFilters={setFilters} onOpen={open}
        onNew={() => call(`${base}/options`).then((o) => setData((c) => ({ ...c, options: o }))).catch((e) => setErr(e.message))} />
      {data.options && canWrite(data.list) ? (
        <NewPaymentForm options={data.options} busy={busy} onCancel={() => setData((c) => ({ ...c, options: null }))}
          onCreate={(body) => run(call(base, { method: "POST", body }).then((d) => {
            setData((c) => ({ ...c, options: null, detail: { ...c.list, payment: d.payment } })); loadList(); }))} />) : null}
      {data.detail ? (
        <PaymentDetail data={data.detail} matches={data.matches} transferView={data.transfer} busy={busy} onAction={act}
          onCustodian={custodian}
          onClose={() => setData((c) => ({ ...c, detail: null }))}
          onSheet={() => run(downloadSheet(`${base}/${encodeURIComponent(pid)}/sheet`).then(() => open(pid)))}
          onMatches={() => Promise.all([call(`${base}/${encodeURIComponent(pid)}/matches`),
            data.detail?.payment?.custodian?.transfer_id
              ? call(`${base}/${encodeURIComponent(pid)}/custodian-transfer`).catch(() => null) : Promise.resolve(null)])
            .then(([m, t]) => setData((c) => ({ ...c, matches: m, transfer: t || c.transfer })))
            .catch((e) => setErr(e.message))} />) : null}
      {err ? <p className="text-sm text-[#9B2335]" role="alert">{err}</p> : null}
      {isMember(data.list) ? null : <p className="text-xs text-text-muted">Approvals also appear in your task inbox. {labelOf(vocab(data.list, "statuses"), "pending_approval")} needs a second person — never the one who made it.</p>}
    </div>
  );
}

export { accountText };
