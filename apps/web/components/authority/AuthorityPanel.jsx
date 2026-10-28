"use client";

/**
 * AuthorityPanel — a household's (or one account's) AUTHORITY (REP7B): the
 * current setting and where it comes from, every account's effective
 * authority × custody, the history, and a change request (new authority,
 * effective date, reason) that a SECOND person approves. On an account the
 * request also carries custody and, for another custodian, the receiving
 * custodian's details (the account there by its last four only). Every
 * control renders only from lib/authority.mjs — the server's flags AND its
 * listed actions; labels and the matrix are the envelope's vocabularies.
 */

import { useState } from "react";
import {
  accountChangeBody, authorityOptions, changeControls, custodyOptions, labelOf, needsReceiving, requestControl,
  sourceText, vocab,
} from "@/lib/authority.mjs";

const card = "rounded-[6px] border border-[#ece8dd] bg-white";
const input = "rounded border border-[#E2E8F0] px-3 py-2";
const primary = "rounded bg-navy px-3 py-1 text-white disabled:opacity-50";
const secondary = "rounded border border-navy px-3 py-1 text-navy disabled:opacity-50";

async function call(path, body) {
  const res = await fetch(`/api/authority/${path}`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
  return data;
}

function History({ envelope, rows, onDecide }) {
  const v = vocab(envelope);
  const [note, setNote] = useState("");
  if (!rows.length) return <p className="mt-2 text-sm text-text-muted">No authority change yet.</p>;
  return (
    <table className="mt-2 w-full text-sm">
      <thead className="text-left text-text-muted"><tr><th className="py-1">Setting</th><th>Effective</th><th>Reason</th><th>Status</th><th /></tr></thead>
      <tbody>{rows.map((r) => {
        const c = changeControls(envelope, r);
        return (
          <tr key={r.id} className="border-t border-[#ece8dd] align-top">
            <td className="py-1">{r.kind === "household" ? labelOf(v.authorities, r.authority)
              : `${r.authority_override ? labelOf(v.authorities, r.authority_override) : "Follows the household"} · ${labelOf(v.custodies, r.custody)}`}
              {r.receiving ? <span className="block text-xs text-text-muted">{r.receiving.custodian_name} · DTC {r.receiving.participant_number}{r.receiving.account_masked ? ` · ${r.receiving.account_masked}` : ""}</span> : null}</td>
            <td>{r.effective_from}</td>
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
  const v = vocab(envelope);
  const auths = authorityOptions(envelope);
  const [form, setForm] = useState({ authority: "", authority_override: "", custody: envelope?.effective?.custody || "",
    effective_from: "", reason: "", receiving: {} });
  const set = (k, val) => setForm({ ...form, [k]: val });
  const setR = (k, val) => setForm({ ...form, receiving: { ...form.receiving, [k]: val } });
  if (kind === "household") {
    return (
      <div className="mt-3 flex flex-wrap items-end gap-2 text-sm">
        <select className={input} value={form.authority} onChange={(e) => set("authority", e.target.value)}>
          <option value="">Choose an authority</option>
          {auths.map((a) => <option key={a.key} value={a.key}>{a.label}</option>)}
        </select>
        <input className={input} type="date" value={form.effective_from} onChange={(e) => set("effective_from", e.target.value)} />
        <input className={`${input} min-w-[16rem]`} placeholder="Reason" value={form.reason} onChange={(e) => set("reason", e.target.value)} />
        <button type="button" className={primary} disabled={!form.authority || !form.effective_from || !form.reason.trim()}
          onClick={() => onSubmit({ authority: form.authority, effective_from: form.effective_from, reason: form.reason.trim() })}>Send for approval</button>
      </div>
    );
  }
  const custodies = custodyOptions(envelope, form.authority_override || null);
  return (
    <div className="mt-3 space-y-2 text-sm">
      <div className="flex flex-wrap items-end gap-2">
        <select className={input} value={form.authority_override} onChange={(e) => set("authority_override", e.target.value)}>
          <option value="">Follow the household</option>
          {auths.map((a) => <option key={a.key} value={a.key}>{a.label}</option>)}
        </select>
        <select className={input} value={form.custody} onChange={(e) => set("custody", e.target.value)}>
          <option value="">Choose custody</option>
          {custodies.map((c) => <option key={c.key} value={c.key}>{c.label}</option>)}
        </select>
        <input className={input} type="date" value={form.effective_from} onChange={(e) => set("effective_from", e.target.value)} />
        <input className={`${input} min-w-[16rem]`} placeholder="Reason" value={form.reason} onChange={(e) => set("reason", e.target.value)} />
      </div>
      {needsReceiving(form.custody) ? (
        <div className="flex flex-wrap gap-2">
          <input className={input} placeholder="Receiving custodian" value={form.receiving.custodian_name || ""} onChange={(e) => setR("custodian_name", e.target.value)} />
          <input className={input} placeholder="DTC / participant number" value={form.receiving.participant_number || ""} onChange={(e) => setR("participant_number", e.target.value)} />
          <input className={input} placeholder="Agent bank" value={form.receiving.agent_bank || ""} onChange={(e) => setR("agent_bank", e.target.value)} />
          <input className={input} placeholder="Account there — last four only" maxLength={4} value={form.receiving.account_last4 || ""} onChange={(e) => setR("account_last4", e.target.value)} />
          <input className={input} placeholder="Contra / executing broker" value={form.receiving.contra_broker || ""} onChange={(e) => setR("contra_broker", e.target.value)} />
        </div>) : null}
      <p className="text-xs text-text-muted">{v.calendar_note}</p>
      <button type="button" className={primary} disabled={!form.custody || !form.effective_from || !form.reason.trim()}
        onClick={() => onSubmit(accountChangeBody(form))}>Send for approval</button>
    </div>
  );
}

export default function AuthorityPanel({ initialEnvelope, kind = "household" }) {
  const [env, setEnv] = useState(initialEnvelope);
  const [asking, setAsking] = useState(false);
  const [msg, setMsg] = useState(null);
  if (!env || typeof env !== "object") return null;
  const v = vocab(env);
  const history = Array.isArray(env.history) ? env.history : [];
  const subjectPath = kind === "household" ? `households/${env.household?.id}` : `accounts/${env.account?.id}`;

  async function reload() {
    const res = await fetch(`/api/authority/${subjectPath}`);
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
    try {
      const out = await call(`changes/${r.id}/${outcome}`, { note: note || undefined });
      const n = Array.isArray(out?.change?.withdrawn) ? out.change.withdrawn.length : 0;
      setMsg(outcome === "approve" ? `Approved${n ? ` — ${n} open proposal(s) the new authority does not allow were withdrawn` : ""}.` : "Rejected.");
      await reload();
    } catch (e) { setMsg(e.message); }
  }

  const eff = env.effective;
  return (
    <div className={`${card} p-6`}>
      <h2 className="text-lg font-semibold text-navy">Authority</h2>
      {kind === "household" ? (
        <p className="mt-1 text-sm">{env.current?.authority
          ? <>{labelOf(v.authorities, env.current.authority)} <span className="text-text-muted">since {env.current.effective_from}</span></>
          : <span className="text-text-muted">{env.current?.note}</span>}</p>
      ) : (
        <p className="mt-1 text-sm">{labelOf(v.authorities, eff?.authority)} · {labelOf(v.custodies, eff?.custody)}
          <span className="block text-xs text-text-muted">{sourceText(env, eff)}{eff?.valid === false ? " — not a valid setting: every trade path refuses" : ""}</span>
          {eff?.receiving ? <span className="block text-xs text-text-muted">Receiving custodian {eff.receiving.custodian_name} · DTC {eff.receiving.participant_number}{eff.receiving.account_masked ? ` · ${eff.receiving.account_masked}` : ""}</span> : null}</p>
      )}
      {kind === "household" && Array.isArray(env.accounts) && env.accounts.length ? (
        <table className="mt-3 w-full text-sm">
          <thead className="text-left text-text-muted"><tr><th className="py-1">Account</th><th>Authority</th><th>Custody</th><th>From</th></tr></thead>
          <tbody>{env.accounts.map((a) => (
            <tr key={a.id} className="border-t border-[#ece8dd]">
              <td className="py-1"><a className="text-navy underline" href={`/portfolio/accounts/${a.id}`}>{a.account_number_masked}</a>{a.is_held_away ? " · held away" : ""}</td>
              <td>{labelOf(v.authorities, a.authority)}</td><td>{labelOf(v.custodies, a.custody)}</td>
              <td className="text-text-muted">{labelOf(v.sources, a.authority_source)}</td>
            </tr>
          ))}</tbody>
        </table>
      ) : null}
      {msg ? <p className="mt-2 text-sm text-navy">{msg}</p> : null}
      {requestControl(env) ? (asking
        ? <RequestForm envelope={env} kind={kind} onSubmit={submit} />
        : <button type="button" className={`${secondary} mt-3 text-sm`} onClick={() => setAsking(true)}>Request a change</button>) : null}
      <h3 className="mt-5 font-medium text-navy">History</h3>
      <History envelope={env} rows={history} onDecide={decide} />
    </div>
  );
}
