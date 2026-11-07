"use client";

/**
 * LongShortPanel — REP13. The account's Long/short panel (eligibility with each piece, the margin agreement on the
 * member's suitability profile, the settings with their history, the short book) and the long/short part of a
 * proposal (both books, borrow cost and the cost comparison, tracking error, §1259 blocks, harvest, the six risks,
 * the margin requirement, the member summary, and the adviser's acknowledgement). Controls from lib/longShort.mjs
 * (fail closed); labels and numbers exactly as the server states them. Light / cream only.
 */

import { useState } from "react";
import { ackReady, eligibilityOf, labelOf, panelControls, proposalControls, sideText, teText, vocab } from "@/lib/longShort.mjs";

const card = "rounded-[6px] border border-[#ece8dd] bg-white p-5";
const input = "rounded border border-[#E2E8F0] px-3 py-2";
const primary = "rounded bg-navy px-3 py-1 text-white disabled:opacity-50";
const secondary = "rounded border border-[#E2E8F0] px-3 py-1 text-navy disabled:opacity-50";

async function send(path, method = "GET", body) {
  const res = await fetch(`/api/long-short/${path}`, {
    method, headers: body ? { "Content-Type": "application/json" } : undefined, body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || "Request failed");
  return data;
}

function Eligibility({ envelope }) {
  const e = eligibilityOf(envelope);
  return (
    <section className={card}>
      <h2 className="font-serif text-xl text-navy">Eligibility</h2>
      <p className="mt-1 text-sm">{e.eligible ? "Eligible for long/short." : "Not eligible for long/short."}</p>
      <ul className="mt-3 space-y-1 text-sm">
        {e.pieces.map((p) => (
          <li key={p.key}><span className={p.ok ? "text-[#2D6A4F]" : "text-[#9B2335]"}>{p.ok ? "Yes" : "No"}</span> — {p.label}
            <span className="text-text-muted"> ({p.detail})</span></li>
        ))}
      </ul>
      {e.missing.length ? <ul className="mt-3 list-disc pl-5 text-sm text-[#9B2335]">{e.missing.map((m) => <li key={m}>{m}</li>)}</ul> : null}
    </section>
  );
}

function SettingsForm({ envelope, onSave }) {
  const c = panelControls(envelope);
  const v = vocab(envelope);
  const s = envelope?.draft || envelope?.settings || {};
  const [f, setF] = useState({ extension: s.extension || "130_30", gross_long: s.gross_long || "", gross_short: s.gross_short || "",
    tracking_error_limit: s.tracking_error_limit || "", max_short_per_name: s.max_short_per_name || "",
    borrow_cost_ceiling_bps: s.borrow_cost_ceiling_bps || "", note: s.note || "" });
  if (!c.saveSettings && !c.saveDraft) return null;
  const body = () => ({ extension: f.extension, tracking_error_limit: f.tracking_error_limit, max_short_per_name: f.max_short_per_name,
    borrow_cost_ceiling_bps: f.borrow_cost_ceiling_bps, note: f.note || null,
    ...(f.extension === "custom" ? { gross_long: f.gross_long, gross_short: f.gross_short } : {}),
    exclusions: (s.exclusions || []) });
  const field = (k, label) => (
    <label className="block text-sm">{label}<input className={`${input} mt-1 w-full`} value={f[k]} onChange={(e) => setF({ ...f, [k]: e.target.value })} /></label>
  );
  return (
    <div className="mt-4 grid gap-3 sm:grid-cols-2">
      <label className="block text-sm">Extension
        <select className={`${input} mt-1 w-full`} value={f.extension} onChange={(e) => setF({ ...f, extension: e.target.value })}>
          {(v.extensions || []).map((x) => <option key={x.key} value={x.key}>{x.label}</option>)}
        </select>
      </label>
      {f.extension === "custom" ? <>{field("gross_long", "Gross long (fraction)")}{field("gross_short", "Gross short (fraction)")}</> : null}
      {field("tracking_error_limit", "Tracking-error limit (fraction a year)")}
      {field("max_short_per_name", "Maximum short per name (fraction of capital)")}
      {field("borrow_cost_ceiling_bps", "Borrow-cost ceiling (bps a year)")}
      {field("note", "Note")}
      <div className="flex gap-2 sm:col-span-2">
        {c.saveDraft ? <button type="button" className={secondary} onClick={() => onSave?.(body(), "draft")}>Save draft</button> : null}
        {c.saveSettings ? <button type="button" className={primary} onClick={() => onSave?.(body(), "saved")}>Save settings</button> : null}
      </div>
    </div>
  );
}

export function AccountLongShortView({ envelope, onSave, onMargin }) {
  const c = panelControls(envelope);
  const v = vocab(envelope);
  const st = envelope?.settings;
  const ma = envelope?.margin_agreement;
  const book = Array.isArray(envelope?.short_book) ? envelope.short_book : [];
  return (
    <div className="space-y-4">
      <Eligibility envelope={envelope} />
      <section className={card}>
        <h2 className="font-serif text-xl text-navy">Margin agreement</h2>
        {ma ? (
          <p className="mt-1 text-sm">{ma.status_label} — dated {ma.agreement_date} (document {ma.document_id})</p>
        ) : <p className="mt-1 text-sm text-text-muted">No margin agreement is recorded on the member’s suitability profile.</p>}
        <div className="mt-3 flex gap-2">
          {c.recordMargin ? <button type="button" className={secondary} onClick={() => onMargin?.("record")}>Record margin agreement</button> : null}
          {c.removeMargin ? <button type="button" className={secondary} onClick={() => onMargin?.("remove")}>Remove</button> : null}
          {c.approveMargin ? <button type="button" className={primary} onClick={() => onMargin?.("approve")}>Approve</button> : null}
          {c.rejectMargin ? <button type="button" className={secondary} onClick={() => onMargin?.("reject")}>Reject</button> : null}
        </div>
      </section>
      <section className={card}>
        <h2 className="font-serif text-xl text-navy">Settings</h2>
        {st ? (
          <dl className="mt-2 grid grid-cols-2 gap-1 text-sm">
            <dt>Extension</dt><dd>{labelOf(v.extensions, st.extension)}</dd>
            <dt>Tracking-error limit</dt><dd>{st.tracking_error_limit}</dd>
            <dt>Maximum short per name</dt><dd>{st.max_short_per_name}</dd>
            <dt>Borrow-cost ceiling</dt><dd>{st.borrow_cost_ceiling_bps} bps</dd>
            <dt>Version</dt><dd>{st.version_no}</dd>
          </dl>
        ) : <p className="mt-1 text-sm text-text-muted">No settings saved.</p>}
        <SettingsForm envelope={envelope} onSave={onSave} />
      </section>
      <section className={card}>
        <h2 className="font-serif text-xl text-navy">Short book</h2>
        {book.length ? (
          <table className="mt-2 w-full text-sm">
            <thead><tr><th className="text-left">Security</th><th className="text-right">Shares</th><th className="text-right">Unrealized</th><th>Term</th></tr></thead>
            <tbody>{book.map((x) => (
              <tr key={x.security_id}><td>{x.label}</td><td className="text-right">{x.signed_quantity}</td>
                <td className="text-right">{x.unrealized ?? "needs a price"}</td><td>short-term</td></tr>))}</tbody>
          </table>
        ) : <p className="mt-1 text-sm text-text-muted">No short positions.</p>}
      </section>
    </div>
  );
}

export function LongShortProposalView({ envelope, onAcknowledge, onExecute }) {
  const c = proposalControls(envelope);
  const d = envelope?.details;
  const [ticked, setTicked] = useState([]);
  if (!d || typeof d !== "object") return null;
  const disc = d.disclosures || {};
  const toggle = (k) => setTicked(ticked.includes(k) ? ticked.filter((x) => x !== k) : [...ticked, k]);
  return (
    <div className="space-y-4">
      <section className={card}>
        <h2 className="font-serif text-xl text-navy">Long/short</h2>
        <p className="mt-1 text-sm">{disc.structure}</p>
        <p className="mt-1 text-sm">Tracking error vs the model: {teText(d)}</p>
        {Array.isArray(d.blocking_labels) && d.blocking_labels.length ? (
          <ul className="mt-2 list-disc pl-5 text-sm text-[#9B2335]">{d.blocking_labels.map((b) => <li key={b.key}>{b.label}</li>)}</ul>
        ) : null}
        {Array.isArray(d.constructive_sale) && d.constructive_sale.length ? (
          <ul className="mt-2 list-disc pl-5 text-sm text-[#9B2335]">{d.constructive_sale.map((b) => <li key={b.security_id}>{b.text}</li>)}</ul>
        ) : null}
      </section>
      <section className={card}>
        <h2 className="font-serif text-xl text-navy">Borrow and cost comparison</h2>
        <table className="mt-2 w-full text-sm"><tbody>
          {(d.borrow?.lines || []).map((l) => (
            <tr key={l.line}><td>{sideText(envelope, "sell_short", "SELL SHORT")} {l.quantity} {l.label}</td>
              <td className="text-right">{l.rate_bps ? `${l.rate_bps} bps` : "borrow rate needed"}</td>
              <td className="text-right">{l.est_borrow_fee_annual ? `$${l.est_borrow_fee_annual} a year (estimate)` : "—"}</td></tr>))}
          {(d.cost_comparison || []).map((l) => (
            <tr key={l.key}><td>{l.label}</td><td className="text-right" colSpan={2}>{l.amount != null ? `$${l.amount}` : l.basis}</td></tr>))}
        </tbody></table>
      </section>
      <section className={card}>
        <h2 className="font-serif text-xl text-navy">Disclosures</h2>
        <ul className="mt-2 list-disc pl-5 text-sm">{(disc.risks || []).map((r) => <li key={r.key}>{r.text}</li>)}</ul>
        <p className="mt-2 text-sm">{disc.borrow_estimate?.text}</p>
        <p className="mt-1 text-sm">Margin requirement — initial: {d.margin?.initial?.text}; maintenance: {d.margin?.maintenance?.text}</p>
        <p className="mt-2 text-sm italic">{d.member_summary}</p>
        {d.acknowledged_at ? <p className="mt-2 text-sm text-[#2D6A4F]">Acknowledged {d.acknowledged_at}</p> : null}
        {c.acknowledge ? (
          <div className="mt-3 space-y-1 text-sm">
            {(d.acknowledgement_items || []).map((x) => (
              <label key={x.key} className="block"><input type="checkbox" checked={ticked.includes(x.key)} onChange={() => toggle(x.key)} /> {x.label}</label>))}
            <button type="button" className={primary} disabled={!ackReady(envelope, ticked)} onClick={() => onAcknowledge?.(ticked)}>Acknowledge</button>
          </div>
        ) : null}
        <div className="mt-3 flex gap-2">
          {c.executeAdapter ? <button type="button" className={primary} onClick={() => onExecute?.("adapter")}>Route to the custodian</button> : null}
          {c.executeManual ? <button type="button" className={secondary} onClick={() => onExecute?.("manual")}>Route manually (order sheet)</button> : null}
        </div>
      </section>
    </div>
  );
}

export function AccountLongShort({ accountId, initialEnvelope }) {
  const [env, setEnv] = useState(initialEnvelope);
  const [error, setError] = useState(null);
  const reload = async () => setEnv(await send(`accounts/${encodeURIComponent(accountId)}`));
  const onSave = async (body, status) => {
    try {
      setError(null);
      await send(`accounts/${encodeURIComponent(accountId)}/settings${status === "draft" ? "/draft" : ""}`, status === "draft" ? "PUT" : "POST", body);
      await reload();
    } catch (e) { setError(e.message); }
  };
  const onMargin = async (what) => {
    try {
      setError(null);
      const rec = env?.margin_agreement;
      if (what === "record") {
        const agreement_date = window.prompt("Agreement date (YYYY-MM-DD)");
        const document_id = window.prompt("Document id of the signed agreement");
        if (!agreement_date || !document_id) return;
        await send(`accounts/${encodeURIComponent(accountId)}/margin-agreement`, "POST", { agreement_date, document_id });
      } else if (what === "remove") {
        const reason = window.prompt("Why is the agreement removed?");
        if (!reason) return;
        await send(`accounts/${encodeURIComponent(accountId)}/margin-agreement/remove`, "POST", { reason });
      } else if (rec) {
        const note = what === "reject" ? window.prompt("Why is it rejected?") : null;
        if (what === "reject" && !note) return;
        await send(`margin-agreements/${encodeURIComponent(rec.id)}/${what}`, "POST", what === "reject" ? { reason: note } : {});
      }
      await reload();
    } catch (e) { setError(e.message); }
  };
  return (
    <div>
      {error ? <p className="mb-3 text-sm text-[#9B2335]">{error}</p> : null}
      <AccountLongShortView envelope={env} onSave={onSave} onMargin={onMargin} />
    </div>
  );
}

export function LongShortProposal({ proposalId, initialEnvelope }) {
  const [env, setEnv] = useState(initialEnvelope);
  const [error, setError] = useState(null);
  const reload = async () => setEnv(await send(`proposals/${encodeURIComponent(proposalId)}`));
  const onAcknowledge = async (items) => {
    try { setError(null); await send(`proposals/${encodeURIComponent(proposalId)}/acknowledge`, "POST", { items }); await reload(); } catch (e) { setError(e.message); }
  };
  const onExecute = async (route) => {
    try { setError(null); await send(`proposals/${encodeURIComponent(proposalId)}/execute`, "POST", { route }); await reload(); } catch (e) { setError(e.message); }
  };
  return (
    <div>
      {error ? <p className="mb-3 text-sm text-[#9B2335]">{error}</p> : null}
      <LongShortProposalView envelope={env} onAcknowledge={onAcknowledge} onExecute={onExecute} />
    </div>
  );
}
