"use client";

/**
 * LotMethodPanel — a household's default LOT METHOD, or one account's override
 * (LOTMETHOD): the method in effect and where it comes from (no method set =
 * today's rule), the gain preference, the custodian's own cost-basis method
 * and the securities it carries at average cost, the history, and a change
 * request a SECOND person approves. Every control renders only from
 * lib/lotMethod.mjs — the server's flags AND its listed actions; labels are
 * the envelope's vocabularies.
 */

import { useState } from "react";
import {
  accountChangeBody, changeControls, custodianOptions, custodianSources, householdChangeBody, labelOf, methodOptions,
  needsTarget, preferenceOptions, requestControl, vocab,
} from "@/lib/lotMethod.mjs";

const card = "rounded-[6px] border border-[#ece8dd] bg-white";
const input = "rounded border border-[#E2E8F0] px-3 py-2";
const primary = "rounded bg-navy px-3 py-1 text-white disabled:opacity-50";
const secondary = "rounded border border-navy px-3 py-1 text-navy disabled:opacity-50";

async function call(path, body) {
  const res = await fetch(`/api/lot-method/${path}`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
  return data;
}

function settingText(r, v) {
  if (r.kind === "household") {
    return `${labelOf(v.methods, r.lot_method)} · ${labelOf(v.gain_preferences, r.gain_preference)}`
      + (r.realize_gains_target ? ` (${r.realize_gains_target})` : "");
  }
  return `${r.lot_method_override ? labelOf(v.methods, r.lot_method_override) : "Follows the household"}`
    + ` · custodian: ${labelOf(v.custodian_methods, r.custodian_cost_basis_method)}`
    + (r.average_cost_security_ids?.length ? ` · ${r.average_cost_security_ids.length} at average cost` : "");
}

function History({ envelope, rows, onDecide }) {
  const v = vocab(envelope);
  const [note, setNote] = useState("");
  if (!rows.length) return <p className="mt-2 text-sm text-text-muted">No lot-method change yet.</p>;
  return (
    <table className="mt-2 w-full text-sm">
      <thead className="text-left text-text-muted"><tr><th className="py-1">Setting</th><th>Reason</th><th>Status</th><th /></tr></thead>
      <tbody>{rows.map((r) => {
        const c = changeControls(envelope, r);
        return (
          <tr key={r.id} className="border-t border-[#ece8dd] align-top">
            <td className="py-1">{settingText(r, v)}</td>
            <td>{r.reason}{r.decision_note ? <span className="block text-xs text-text-muted">{r.decision_note}</span> : null}</td>
            <td>{labelOf(v.statuses, r.status)}{r.superseded ? <span className="block text-xs text-text-muted">superseded</span> : null}</td>
            <td>{c.approve || c.reject ? (
              <div className="flex flex-col gap-1">
                <input className={input} placeholder="Note (needed to reject)" value={note} onChange={(e) => setNote(e.target.value)} />
                <div className="flex gap-2">
                  {c.approve ? <button type="button" className={primary} onClick={() => onDecide(r, "approve", note)}>Approve</button> : null}
                  {c.reject ? <button type="button" className={secondary} onClick={() => onDecide(r, "reject", note)}>Reject</button> : null}
                </div>
              </div>) : null}</td>
          </tr>
        );
      })}</tbody>
    </table>
  );
}

export function RequestForm({ envelope, kind, onSubmit }) {
  const methods = methodOptions(envelope);
  const prefs = preferenceOptions(envelope);
  const [form, setForm] = useState({ lot_method: "", gain_preference: "", realize_gains_target: "", reason: "",
    lot_method_override: "", gain_preference_override: "", custodian_cost_basis_method: "unknown",
    custodian_method_source: "", average_cost_security_ids: [] });
  const set = (k, val) => setForm({ ...form, [k]: val });
  const pref = kind === "household" ? form.gain_preference : form.gain_preference_override;
  const targetOk = !needsTarget(pref) || /^\d+(\.\d+)?$/.test(form.realize_gains_target.trim());
  if (kind === "household") {
    return (
      <div className="mt-3 flex flex-wrap items-end gap-2 text-sm">
        <select className={input} value={form.lot_method} onChange={(e) => set("lot_method", e.target.value)}>
          <option value="">Choose a method</option>
          {methods.map((m) => <option key={m.key} value={m.key}>{m.label}</option>)}
        </select>
        <select className={input} value={form.gain_preference} onChange={(e) => set("gain_preference", e.target.value)}>
          <option value="">Gain preference</option>
          {prefs.map((m) => <option key={m.key} value={m.key}>{m.label}</option>)}
        </select>
        {needsTarget(form.gain_preference) ? <input className={input} placeholder="Target gain ($, you enter it)" value={form.realize_gains_target} onChange={(e) => set("realize_gains_target", e.target.value)} /> : null}
        <input className={`${input} min-w-[16rem]`} placeholder="Reason" value={form.reason} onChange={(e) => set("reason", e.target.value)} />
        <button type="button" className={primary} disabled={!form.lot_method || !form.reason.trim() || !targetOk}
          onClick={() => onSubmit(householdChangeBody(form))}>Send for approval</button>
      </div>
    );
  }
  const custodian = custodianOptions(envelope);
  const sources = custodianSources(envelope);
  return (
    <div className="mt-3 space-y-2 text-sm">
      <div className="flex flex-wrap items-end gap-2">
        <select className={input} value={form.lot_method_override} onChange={(e) => set("lot_method_override", e.target.value)}>
          <option value="">Follow the household</option>
          {methods.map((m) => <option key={m.key} value={m.key}>{m.label}</option>)}
        </select>
        <select className={input} value={form.gain_preference_override} onChange={(e) => set("gain_preference_override", e.target.value)}>
          <option value="">Preference: follow the household</option>
          {prefs.map((m) => <option key={m.key} value={m.key}>{m.label}</option>)}
        </select>
        {needsTarget(form.gain_preference_override) ? <input className={input} placeholder="Target gain ($, you enter it)" value={form.realize_gains_target} onChange={(e) => set("realize_gains_target", e.target.value)} /> : null}
      </div>
      <div className="flex flex-wrap items-end gap-2">
        <select className={input} value={form.custodian_cost_basis_method} onChange={(e) => set("custodian_cost_basis_method", e.target.value)}>
          {custodian.map((m) => <option key={m.key} value={m.key}>Custodian default: {m.label}</option>)}
        </select>
        {form.custodian_cost_basis_method !== "unknown" ? (
          <select className={input} value={form.custodian_method_source} onChange={(e) => set("custodian_method_source", e.target.value)}>
            <option value="">Where it came from</option>
            {sources.map((m) => <option key={m.key} value={m.key}>{m.label}</option>)}
          </select>) : null}
        <input className={`${input} min-w-[16rem]`} placeholder="Reason" value={form.reason} onChange={(e) => set("reason", e.target.value)} />
      </div>
      <button type="button" className={primary}
        disabled={!form.reason.trim() || !targetOk || (form.custodian_cost_basis_method !== "unknown" && !form.custodian_method_source)}
        onClick={() => onSubmit(accountChangeBody(form))}>Send for approval</button>
    </div>
  );
}

export default function LotMethodPanel({ initialEnvelope, kind = "household" }) {
  const [env, setEnv] = useState(initialEnvelope);
  const [asking, setAsking] = useState(false);
  const [msg, setMsg] = useState(null);
  if (!env || typeof env !== "object") return null;
  const v = vocab(env);
  const history = Array.isArray(env.history) ? env.history : [];
  const subjectPath = kind === "household" ? `households/${env.household?.id}` : `accounts/${env.account?.id}`;
  const eff = kind === "household" ? env.current : env.effective;

  async function reload() {
    const res = await fetch(`/api/lot-method/${subjectPath}`);
    const data = await res.json().catch(() => ({}));
    if (res.ok) { setEnv(data); setAsking(false); } else setMsg(data.error || `Reload failed (${res.status})`);
  }
  async function submit(body) {
    setMsg(null);
    try { await call(`${subjectPath}/changes`, body); setMsg("Sent — a second person approves it."); await reload(); }
    catch (e) { setMsg(e.message); }
  }
  async function decide(r, outcome, note) {
    setMsg(null);
    try { await call(`changes/${r.id}/${outcome}`, { note: note || undefined }); setMsg(outcome === "approve" ? "Approved." : "Rejected."); await reload(); }
    catch (e) { setMsg(e.message); }
  }

  return (
    <div className={`${card} p-6`}>
      <h2 className="text-lg font-semibold text-navy">Lot method</h2>
      {eff ? (
        <div className="mt-2 text-sm">
          <p>{labelOf(v.methods, eff.lot_method)} · {labelOf(v.gain_preferences, eff.gain_preference)}
            {eff.realize_gains_target ? ` — target ${eff.realize_gains_target}` : ""}</p>
          <p className="text-xs text-text-muted">{labelOf(v.sources, eff.lot_method_source)}{eff.today_rule === true && v.today_rule ? ` — ${v.today_rule}` : ""}</p>
          {kind === "account" ? (
            <p className="mt-1 text-xs text-text-muted">Custodian default: {labelOf(v.custodian_methods, eff.custodian_cost_basis_method)}
              {eff.custodian_method_source ? ` (${labelOf(v.custodian_sources, eff.custodian_method_source)})` : ""}
              {Array.isArray(eff.average_cost_security_ids) && eff.average_cost_security_ids.length ? ` · ${eff.average_cost_security_ids.length} security(ies) at average cost` : ""}</p>
          ) : null}
          {eff.mismatch ? <p className="mt-1 text-xs text-[#9B2335]">{eff.mismatch.message}</p> : null}
          {v.precedence ? <p className="mt-1 text-xs text-text-muted">Precedence: {v.precedence}</p> : null}
        </div>
      ) : null}
      <History envelope={env} rows={history} onDecide={decide} />
      {requestControl(env) ? (asking ? <RequestForm envelope={env} kind={kind} onSubmit={submit} />
        : <button type="button" className={`${secondary} mt-3`} onClick={() => setAsking(true)}>Request a change</button>) : null}
      {msg ? <p className="mt-2 text-sm text-text-muted">{msg}</p> : null}
    </div>
  );
}
