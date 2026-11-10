"use client";

/**
 * TreasuryPayees — Household → Treasury → Payees (CASH08), inside the EXISTING Treasury tab (no second tab): the
 * household's payee registry (status, SLOA state, flags), the detail (last four only, the custodian's reference, the
 * SLOA, the history of every state), the new-payee form, the bank-detail change request, and the CALL-BACK
 * ATTESTATION form an approver fills in (who they called, the number ON FILE, when). Approve / Reject appear only when
 * the server lists them on the payee (the S30 floor — a maker / the requester never sees them); the approval tasks are
 * also in the existing inbox. A member reads their family's active payees read-only — name, kind, last four — with the
 * cash.third_party disclosure. Third-party payments are not enabled: nothing here pays anyone. Every decision comes
 * from lib/cashPayees.mjs, which fails closed. Reads go through the Next.js proxy (/api/cash/households/{id}/payees…).
 * Light / cream.
 *
 * ``initial`` ({list, detail}) renders without fetching (render tests).
 */

import { useCallback, useEffect, useState } from "react";
import {
  attestationBody, bankChangeBody, canWrite, detailOf, disclosureText, isMember, lastFourText, newOffer,
  payeeActions, payeeBody, payeeRows, readable, vocab,
} from "@/lib/cashPayees.mjs";

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

export function PayeeList({ data, onOpen, onNew }) {
  if (!readable(data)) return null;
  const rows = payeeRows(data);
  const member = isMember(data);
  const disc = disclosureText(data);
  const tp = data?.third_party;
  return (
    <section id="treasury-payees" className="space-y-2" aria-label="Payees">
      <div className="flex flex-wrap items-baseline gap-2">
        <h3 className="mr-auto text-lg font-semibold text-navy" style={serif}>Payees</h3>
        {newOffer(data) ? <button type="button" className={btn} onClick={onNew}>New payee</button> : null}
      </div>
      {tp && tp.enabled !== true && tp.reason ? (
        <p className="text-sm text-[#334155]" data-third-party="off">Third-party payments: {tp.reason}.</p>) : null}
      {disc ? <p className="text-xs text-text-muted" data-label="cash.third_party">{disc}</p> : null}
      {rows.length === 0 ? <p className={`${card} text-sm text-text-muted`} data-empty="payees">No payees.</p> : (
        <table className={`${card} w-full text-sm`}>
          <thead className="text-left text-text-muted"><tr><th className="py-1">Payee</th><th>Kind</th><th>Account</th>
            <th>Status</th>{member ? null : <><th>SLOA</th><th>Flags</th></>}</tr></thead>
          <tbody>{rows.map((r) => (
            <tr key={r.id} className={`border-t border-[#ece8dd] align-top ${member ? "" : "cursor-pointer"}`}
              onClick={member ? undefined : () => onOpen?.(r.id)} data-payee={r.id}>
              <td className="py-1">{r.name}</td><td>{r.kind}</td><td>{r.lastFour}</td><td>{r.status}</td>
              {member ? null : <><td>{r.sloa}</td><td>{r.flags.length ? r.flags.join(" · ") : "—"}</td></>}
            </tr>))}
          </tbody>
        </table>)}
    </section>
  );
}

export function NewPayeeForm({ data, onCreate, onCancel, busy = false }) {
  const [form, setForm] = useState({ kind: "", display_name: "", legal_name: "", contact_name: "", contact_email: "",
    contact_phone: "", bank_name: "", account_type: "", last_four: "", payee_reference: "", notes: "" });
  const [err, setErr] = useState(null);
  if (!canWrite(data)) return null;
  const set = (k) => (e) => setForm((c) => ({ ...c, [k]: e.target.value }));
  const field = (k, label, extra = {}) => (
    <label className="block text-sm">{label}
      <input className={`${input} ml-2`} aria-label={label} value={form[k]} onChange={set(k)} {...extra} /></label>);
  const submit = () => {
    const b = payeeBody(data, form);
    if (!b.ok) { setErr(b.error); return; }
    setErr(null);
    onCreate?.(b.body);
  };
  return (
    <section className={`${card} space-y-2`} aria-label="New payee">
      <h4 className="font-semibold text-navy">New payee — approved by a second person before it is active</h4>
      <label className="block text-sm">Kind
        <select className={`${input} ml-2`} aria-label="Kind" value={form.kind} onChange={set("kind")}>
          <option value="">Choose</option>
          {vocab(data, "kinds").map((k) => <option key={k.key} value={k.key}>{k.label}</option>)}
        </select></label>
      {field("display_name", "Display name")}
      {field("legal_name", "Legal name")}
      {field("contact_name", "Contact")}
      {field("contact_email", "Contact email")}
      {field("contact_phone", "Call-back number on file")}
      {field("bank_name", "Bank")}
      <label className="block text-sm">Account type
        <select className={`${input} ml-2`} aria-label="Account type" value={form.account_type} onChange={set("account_type")}>
          <option value="">Choose</option>
          {vocab(data, "account_types").map((k) => <option key={k.key} value={k.key}>{k.label}</option>)}
        </select></label>
      {field("last_four", "Last four digits", { maxLength: 4, inputMode: "numeric" })}
      {field("payee_reference", "Custodian reference (standing instruction)")}
      <p className="text-xs text-text-muted">Never a full account or routing number — the custodian&apos;s reference and the
        last four digits only. The tax form (W-9) belongs in Chancery.</p>
      {err ? <p className="text-sm text-[#9B2335]" role="alert">{err}</p> : null}
      <div className="flex gap-2">
        <button type="button" className={btn} disabled={busy} onClick={submit}>Save draft</button>
        <button type="button" className={btn} onClick={onCancel}>Cancel</button>
      </div>
    </section>
  );
}

export function BankChangeForm({ data, payee, onRequest, onCancel, busy = false }) {
  const [form, setForm] = useState({ bank_name: "", account_type: "", last_four: "", payee_reference: "",
    request_contact: "", request_phone: "", reason: "" });
  const [err, setErr] = useState(null);
  const allowed = payeeActions(data, payee).some((a) => a.key === "request_bank_change");
  if (!allowed) return null;
  const set = (k) => (e) => setForm((c) => ({ ...c, [k]: e.target.value }));
  const go = () => {
    const b = bankChangeBody(data, form);
    if (!b.ok) { setErr(b.error); return; }
    setErr(null);
    onRequest?.(b.body);
  };
  return (
    <section className={`${card} space-y-2`} aria-label="Change bank details">
      <h4 className="font-semibold text-navy">Change bank details — the payee is suspended until a second person calls back</h4>
      <p className="text-xs text-text-muted">Currently {lastFourText(payee?.last_four)}. A request to change bank details is the
        most common route of payment fraud.</p>
      {[["bank_name", "New bank"], ["last_four", "New last four digits"], ["payee_reference", "New custodian reference"],
        ["request_contact", "How the request arrived (who, which channel)"], ["request_phone", "A number given in the request"],
        ["reason", "Reason"]].map(([k, label]) => (
        <label key={k} className="block text-sm">{label}
          <input className={`${input} ml-2`} aria-label={label} value={form[k]} onChange={set(k)} /></label>))}
      {err ? <p className="text-sm text-[#9B2335]" role="alert">{err}</p> : null}
      <div className="flex gap-2">
        <button type="button" className={btn} disabled={busy} onClick={go}>Request the change</button>
        <button type="button" className={btn} onClick={onCancel}>Cancel</button>
      </div>
    </section>
  );
}

export function CallbackForm({ data, payee, onApprove, onReject, busy = false }) {
  const [form, setForm] = useState({ person: "", number: "", called_at: "", note: "" });
  const [err, setErr] = useState(null);
  const acts = payeeActions(data, payee).map((a) => a.key);
  const bc = payee?.bank_change;
  if (!bc || !acts.includes("approve_bank_change")) return null;
  const set = (k) => (e) => setForm((c) => ({ ...c, [k]: e.target.value }));
  const approve = () => {
    const b = attestationBody(form);
    if (!b.ok) { setErr(b.error); return; }
    setErr(null);
    onApprove?.(b.body);
  };
  return (
    <section className={`${card} space-y-2`} aria-label="Call-back attestation">
      <h4 className="font-semibold text-navy">Verify by call-back, then approve</h4>
      <p className="text-sm text-[#334155]">Call the number ON FILE: <span data-on-file="1">{bc.callback_number_on_file || "none on file"}</span>.
        Never a number from the request ({bc.request_contact}).</p>
      <p className="text-xs text-text-muted">Change: {lastFourText(payee.last_four)} → {lastFourText(bc.pending_last_four)}, requested by {bc.requested_by_name || "a colleague"}.</p>
      {[["person", "Person you spoke to"], ["number", "Number you called"], ["called_at", "When you called"]].map(([k, label]) => (
        <label key={k} className="block text-sm">{label}
          <input className={`${input} ml-2`} aria-label={label} value={form[k]} onChange={set(k)} required
            type={k === "called_at" ? "datetime-local" : "text"} /></label>))}
      <label className="block text-sm">Note
        <input className={`${input} ml-2`} aria-label="Note" value={form.note} onChange={set("note")} /></label>
      {err ? <p className="text-sm text-[#9B2335]" role="alert">{err}</p> : null}
      <div className="flex gap-2">
        <button type="button" className={btn} disabled={busy} onClick={approve}>Approve bank change</button>
        {acts.includes("reject_bank_change") ? (
          <button type="button" className={btn} disabled={busy} onClick={() => onReject?.(form.note)}>Reject bank change</button>) : null}
      </div>
    </section>
  );
}

export function SloaForm({ data, payee, onAttach, onCancel, busy = false }) {
  const [form, setForm] = useState({ document_id: "", account_ids: "", signed_on: "", expires_on: "" });
  if (!payeeActions(data, payee).some((a) => a.key === "attach_sloa")) return null;
  const set = (k) => (e) => setForm((c) => ({ ...c, [k]: e.target.value }));
  const go = () => onAttach?.({ document_id: form.document_id.trim(),
    account_ids: form.account_ids.split(",").map((x) => x.trim()).filter(Boolean),
    signed_on: form.signed_on || null, expires_on: form.expires_on || null });
  return (
    <section className={`${card} space-y-2`} aria-label="Attach SLOA">
      <h4 className="font-semibold text-navy">Attach the Standing Letter of Authorization (a Chancery document)</h4>
      {[["document_id", "Chancery document", "text"], ["account_ids", "Source account ids (comma-separated)", "text"],
        ["signed_on", "Signed on", "date"], ["expires_on", "Expires on", "date"]].map(([k, label, type]) => (
        <label key={k} className="block text-sm">{label}
          <input className={`${input} ml-2`} aria-label={label} type={type} value={form[k]} onChange={set(k)} /></label>))}
      <div className="flex gap-2">
        <button type="button" className={btn} disabled={busy} onClick={go}>Attach SLOA</button>
        <button type="button" className={btn} onClick={onCancel}>Cancel</button>
      </div>
    </section>
  );
}

export function PayeeDetail({ data, onAction, onClose, busy = false }) {
  const p = detailOf(data);
  if (!p) return null;
  const member = isMember(data);
  const acts = payeeActions(data, p).filter((a) => !["request_bank_change", "approve_bank_change", "reject_bank_change"].includes(a.key));
  return (
    <section className={`${card} space-y-2`} aria-label="Payee detail" data-payee-detail={p.id}>
      <div className="flex items-baseline gap-2">
        <h4 className="mr-auto font-semibold text-navy" style={serif}>{p.display_name}</h4>
        <button type="button" className="text-sm text-text-muted" onClick={onClose}>Close</button>
      </div>
      <dl className="grid grid-cols-2 gap-1 text-sm">
        <dt className="text-text-muted">Kind</dt><dd>{p.kind_label}</dd>
        <dt className="text-text-muted">Account</dt><dd>{lastFourText(p.last_four)}</dd>
        <dt className="text-text-muted">Status</dt><dd>{p.status_label}</dd>
        {member ? null : <>
          <dt className="text-text-muted">Custodian reference</dt><dd>{p.payee_reference || "—"}</dd>
          <dt className="text-text-muted">Bank</dt><dd>{p.bank_name || "—"}</dd>
          <dt className="text-text-muted">Call-back number on file</dt><dd>{p.contact_phone || "—"}</dd>
          <dt className="text-text-muted">SLOA</dt>
          <dd>{p.sloa?.document_id ? `${p.sloa.document_name || "Document"}${p.sloa.expires_on ? `, expires ${p.sloa.expires_on}` : ""}` : "None on file"}
            {(p.flags || []).length ? <span className="block text-xs text-[#9B2335]">{p.flags.map((f) => f.label).join(" · ")}</span> : null}</dd>
          {p.last_callback ? <><dt className="text-text-muted">Last call-back</dt>
            <dd>{p.last_callback.person}, {p.last_callback.number}, {p.last_callback.at}</dd></> : null}
        </>}
      </dl>
      {acts.length ? (
        <div className="flex flex-wrap gap-2">{acts.map((a) => (
          <button key={a.key} type="button" className={btn} disabled={busy} onClick={() => onAction?.(a.key)}>{a.label}</button>))}
        </div>) : null}
      {member || !(p.history || []).length ? null : (
        <table className="w-full text-xs" aria-label="Payee history">
          <thead className="text-left text-text-muted"><tr><th>From</th><th>Status</th><th>Account</th><th>Reference</th><th>Call-back</th></tr></thead>
          <tbody>{p.history.map((h, i) => (
            <tr key={`${h.from}-${i}`} className="border-t border-[#ece8dd]"><td>{h.from}</td><td>{h.status_label}</td>
              <td>{lastFourText(h.last_four)}{h.pending_last_four ? ` → ${lastFourText(h.pending_last_four)} (pending)` : ""}</td>
              <td>{h.payee_reference || "—"}</td><td>{h.callback_person ? `${h.callback_person}, ${h.callback_at}` : "—"}</td></tr>))}
          </tbody>
        </table>)}
    </section>
  );
}

export default function TreasuryPayees({ householdId, initial = null }) {
  const [list, setList] = useState(initial?.list || null);
  const [detail, setDetail] = useState(initial?.detail || null);
  const [mode, setMode] = useState(initial?.mode || null);
  const [err, setErr] = useState(null);
  const [busy, setBusy] = useState(false);
  const base = householdId ? `/api/cash/households/${encodeURIComponent(householdId)}/payees` : null;

  const reload = useCallback(() => {
    if (!base) return;
    call(base).then(setList).catch((e) => setErr(e.message));
  }, [base]);
  useEffect(() => { if (!initial) reload(); }, [initial, reload]);

  const open = (id) => call(`${base}/${encodeURIComponent(id)}`).then(setDetail).catch((e) => setErr(e.message));
  const run = async (fn) => {
    setBusy(true);
    try { await fn(); setErr(null); reload(); } catch (e) { setErr(e.message); } finally { setBusy(false); }
  };
  const pid = detail?.payee?.id;
  const post = (path, body) => run(async () => {
    const d = await call(`${base}/${encodeURIComponent(pid)}/${path}`, { method: "POST", body: body || {} });
    setDetail((cur) => ({ ...cur, payee: d.payee }));
  });
  const onAction = (key) => {
    if (key === "approve") return post("approve", {});
    if (key === "reject") return post("reject", { note: window.prompt("Why return it to draft?") || "" });
    if (["suspend", "retire", "revoke_sloa"].includes(key)) return post(key, { reason: window.prompt("Reason") || "" });
    if (key === "attach_sloa") { setMode("sloa"); return undefined; }
    return post(key, {});
  };
  if (!readable(list)) return err ? <p className="text-sm text-[#9B2335]" role="alert">{err}</p> : null;
  return (
    <div className="space-y-3">
      <PayeeList data={list} onOpen={open} onNew={() => setMode("new")} />
      {mode === "new" ? <NewPayeeForm data={list} busy={busy} onCancel={() => setMode(null)}
        onCreate={(body) => run(async () => { await call(base, { method: "POST", body }); setMode(null); })} /> : null}
      {detail ? <PayeeDetail data={detail} busy={busy} onAction={onAction} onClose={() => setDetail(null)} /> : null}
      {detail && mode === "sloa" ? <SloaForm data={detail} payee={detail.payee} busy={busy} onCancel={() => setMode(null)}
        onAttach={(body) => { setMode(null); return post("attach_sloa", body); }} /> : null}
      {detail ? <BankChangeForm data={detail} payee={detail.payee} busy={busy} onCancel={() => setMode(null)}
        onRequest={(body) => post("request_bank_change", body)} /> : null}
      {detail ? <CallbackForm data={detail} payee={detail.payee} busy={busy}
        onApprove={(body) => post("bank-change/approve", body)}
        onReject={(note) => post("bank-change/reject", { note })} /> : null}
      {err ? <p className="text-sm text-[#9B2335]" role="alert">{err}</p> : null}
    </div>
  );
}
