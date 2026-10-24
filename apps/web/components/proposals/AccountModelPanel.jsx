"use client";

/**
 * AccountModelPanel — one account's model (REP5): the link and its personal
 * settings, a change awaiting approval, the transition plan, and the account's
 * proposals. Join / Change model offer the REGISTRY first (approved models —
 * nothing is built here). Every control renders only from
 * `accountControls(envelope)` — the server's flags AND its `actions` list. The
 * custodian harvesting option appears only when the server returned it (its
 * capability evidenced 'yes'). Labels are the envelope's vocabularies.
 */

import { useState } from "react";
import { accountControls, engineOptions, labelOf, moneyText, pctText, settingsBody, vocab } from "@/lib/proposals.mjs";

const card = "rounded-[6px] border border-[#ece8dd] bg-white";
const input = "rounded border border-[#E2E8F0] px-3 py-2";
const primary = "rounded bg-navy px-3 py-1 text-white disabled:opacity-50";
const secondary = "rounded border border-navy px-3 py-1 text-navy disabled:opacity-50";

async function call(path, body) {
  const res = await fetch(`/api/${path}`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
  return data;
}

function SettingsView({ link, v }) {
  const s = link?.settings || {};
  const rows = [
    ["Cash floor", pctText(s.cash_floor)],
    ["Drift threshold", s.drift_threshold ? pctText(s.drift_threshold) : "From the model"],
    ["Rebalance frequency", s.rebalance_frequency ? labelOf(v.rebalance_frequencies, s.rebalance_frequency) : "From the model"],
    ["Exclusion policy", labelOf(v.exclusion_policies, s.exclusion_policy)],
    ["Gain budget (per year)", s.gain_budget ? moneyText(s.gain_budget) : "Unlimited"],
    ["Short-term guard", s.avoid_short_term ? `On — wait up to ${s.short_term_wait_days} days` : "Off"],
    ["Harvesting engine", labelOf(v.harvesting_engines, s.harvesting_engine)],
  ];
  return (
    <div>
      <dl className="grid grid-cols-1 gap-x-6 gap-y-2 text-sm md:grid-cols-2">
        {rows.map(([k, val]) => (
          <div key={k} className="flex gap-3"><dt className="w-44 shrink-0 text-text-muted">{k}</dt><dd>{val}</dd></div>
        ))}
      </dl>
      {Array.isArray(s.exclusions) && s.exclusions.length ? (
        <div className="mt-3 text-sm">
          <p className="font-medium text-navy">Exclusions</p>
          <ul className="mt-1">{s.exclusions.map((e) => <li key={e.global_security_id}>{e.label || e.global_security_id} — {e.reason}</li>)}</ul>
        </div>
      ) : null}
    </div>
  );
}

export function SettingsForm({ envelope, mode, onDone }) {
  const v = vocab(envelope);
  const registry = Array.isArray(envelope?.registry) ? envelope.registry : [];
  const engines = engineOptions(envelope);
  const current = envelope?.links?.active?.settings || {};
  const [form, setForm] = useState({ modelId: "", exclusionPolicy: current.exclusion_policy || "pro_rata",
    avoidShortTerm: current.avoid_short_term ?? true, engine: current.harvesting_engine || "hollisworks",
    exclusions: current.exclusions || [] });
  const [exSec, setExSec] = useState("");
  const [exReason, setExReason] = useState("");
  const [msg, setMsg] = useState(null);
  const set = (k) => (e) => setForm({ ...form, [k]: e.target.type === "checkbox" ? e.target.checked : e.target.value });
  const id = envelope?.account?.id;
  async function save() {
    setMsg(null);
    try {
      const settings = settingsBody(form);
      if (mode === "settings") await call(`accounts/${id}/model/settings`, { settings });
      else await call(`accounts/${id}/model/${mode === "join" ? "join" : "change"}`, { model_id: form.modelId, settings });
      onDone?.();
    } catch (err) { setMsg(err.message); }
  }
  return (
    <div className={`${card} space-y-3 p-6 text-sm`}>
      {mode !== "settings" ? (
        <label className="flex flex-col gap-1">Model (from the registry — approved models only)
          <select className={input} value={form.modelId} onChange={set("modelId")}>
            <option value="">Choose a model</option>
            {registry.map((m) => <option key={m.id} value={m.id}>{m.name} (v{m.live_version_no})</option>)}
          </select>
        </label>
      ) : null}
      <div className="grid grid-cols-1 gap-3 md:grid-cols-3">
        <label className="flex flex-col gap-1">Cash floor (%)<input className={input} onChange={set("cashFloorPct")} /></label>
        <label className="flex flex-col gap-1">Drift threshold (%)<input className={input} placeholder="From the model" onChange={set("driftPct")} /></label>
        <label className="flex flex-col gap-1">Gain budget per year ($)<input className={input} placeholder="Unlimited" onChange={set("gainBudget")} /></label>
        <label className="flex flex-col gap-1">Exclusion policy
          <select className={input} value={form.exclusionPolicy} onChange={set("exclusionPolicy")}>
            {(v.exclusion_policies || []).map((p) => <option key={p.key} value={p.key}>{p.label}</option>)}
          </select>
        </label>
        <label className="flex flex-col gap-1">Rebalance frequency
          <select className={input} defaultValue="" onChange={set("frequency")}>
            <option value="">From the model</option>
            {(v.rebalance_frequencies || []).map((p) => <option key={p.key} value={p.key}>{p.label}</option>)}
          </select>
        </label>
        <label className="flex flex-col gap-1">Harvesting engine
          <select className={input} value={form.engine} onChange={set("engine")}>
            {engines.map((o) => <option key={o.key} value={o.key}>{o.label}</option>)}
          </select>
        </label>
        <label className="flex items-center gap-2"><input type="checkbox" checked={form.avoidShortTerm} onChange={set("avoidShortTerm")} />
          Avoid short-term gains</label>
        <label className="flex flex-col gap-1">Wait up to (days)<input className={input} placeholder="30" onChange={set("waitDays")} /></label>
      </div>
      <div>
        <p className="font-medium text-navy">Exclusions</p>
        <ul className="mt-1">{form.exclusions.map((e) => (
          <li key={e.global_security_id}>{e.global_security_id} — {e.reason}{" "}
            <button type="button" className="text-navy underline" onClick={() => setForm({ ...form,
              exclusions: form.exclusions.filter((x) => x.global_security_id !== e.global_security_id) })}>remove</button></li>
        ))}</ul>
        <div className="mt-2 flex flex-wrap gap-2">
          <input className={input} placeholder="Security id" value={exSec} onChange={(e) => setExSec(e.target.value)} />
          <input className={input} placeholder="Reason" value={exReason} onChange={(e) => setExReason(e.target.value)} />
          <button type="button" className={secondary} onClick={() => {
            if (exSec && exReason) { setForm({ ...form, exclusions: [...form.exclusions, { global_security_id: exSec, reason: exReason }] }); setExSec(""); setExReason(""); }
          }}>Add exclusion</button>
        </div>
      </div>
      <button type="button" className={primary} onClick={save}>Send for approval</button>
      {msg ? <p className="text-[#9B2335]">{msg}</p> : null}
    </div>
  );
}

export function PlanView({ plan, v }) {
  if (!plan) return <p className="text-sm text-text-muted">No transition plan yet — it is made when a join is approved.</p>;
  const holdings = Array.isArray(plan.holdings) ? plan.holdings : [];
  return (
    <div className="text-sm">
      <p className="text-text-muted">As of {plan.valuation_as_of || "—"} · embedded gain {moneyText(plan.total_embedded_gain)} ·
        {" "}{plan.years_to_complete ? `${plan.years_to_complete} year(s) to complete` : "nothing to transition"}
        {plan.gain_budget ? ` at ${moneyText(plan.gain_budget)} a year` : ""}</p>
      <table className="mt-2 w-full">
        <thead className="text-left text-text-muted"><tr><th className="py-1">Holding</th><th>Action</th><th>Sell now</th><th>Sell later</th></tr></thead>
        <tbody>{holdings.map((h) => (
          <tr key={`${h.key}-${h.action}`} className="border-t border-[#ece8dd]">
            <td className="py-1">{h.label}</td>
            <td>{labelOf(v.plan_actions, h.action)}</td>
            <td>{h.sell_now && h.sell_now !== "0" ? h.sell_now : "—"}</td>
            <td>{(h.sell_later || []).map((x, i) => <span key={i} className="block">{x.quantity} in {x.year || "later"}{x.on_date ? ` (from ${x.on_date})` : ""}</span>)}</td>
          </tr>
        ))}</tbody>
      </table>
    </div>
  );
}

export default function AccountModelPanel({ initialEnvelope }) {
  const [env, setEnv] = useState(initialEnvelope);
  const [mode, setMode] = useState(null);
  const [note, setNote] = useState("");
  const [reason, setReason] = useState("");
  const [withdrawal, setWithdrawal] = useState("");
  const [msg, setMsg] = useState(null);
  const c = accountControls(env);
  const v = vocab(env);
  const a = env?.account || {};
  const active = env?.links?.active;
  const pending = env?.links?.pending;
  const proposals = Array.isArray(env?.proposals) ? env.proposals : [];

  async function reload() {
    const res = await fetch(`/api/accounts/${a.id}/model`);
    const data = await res.json().catch(() => ({}));
    if (res.ok) { setEnv(data); setMode(null); } else setMsg(data.error || `Reload failed (${res.status})`);
  }
  async function run(path, body) {
    setMsg(null);
    try { const out = await call(path, body); await reload(); return out; } catch (err) { setMsg(err.message); return null; }
  }

  return (
    <div className="space-y-5">
      <div className={`${card} p-6 text-sm`}>
        <p><span className="font-medium text-navy">{a.account_number_masked}</span> · {a.custodian_code} · {a.registration_type}
          {a.is_held_away ? " · held away (manual execution)" : ""}{a.is_discretionary === false ? " · non-discretionary: proposals are advice only" : ""}
          {a.closed ? ` · closed ${a.closed_on}` : ""}</p>
      </div>

      <div className={`${card} p-6`}>
        <h2 className="text-lg font-semibold text-navy">Model</h2>
        {active ? (
          <div className="mt-2 space-y-3">
            <p className="text-sm">Follows <span className="font-medium">{active.model_name}</span> (settings version {active.version_no}, since {String(active.joined_at || "").slice(0, 10)})</p>
            <SettingsView link={active} v={v} />
          </div>
        ) : <p className="mt-2 text-sm text-text-muted">This account does not follow a model.</p>}
        <div className="mt-4 flex flex-wrap gap-2 text-sm">
          {c.join ? <button type="button" className={primary} onClick={() => setMode("join")}>Join a model</button> : null}
          {c.changeModel ? <button type="button" className={secondary} onClick={() => setMode("change")}>Change model</button> : null}
          {c.changeSettings ? <button type="button" className={secondary} onClick={() => setMode("settings")}>Change settings</button> : null}
          {c.generate ? <button type="button" className={secondary} onClick={() => run(`accounts/${a.id}/proposals`, { trigger: "manual" })}>Rebalance now</button> : null}
        </div>
        {c.generate ? (
          <div className="mt-3 flex flex-wrap items-center gap-2 text-sm">
            <input className={input} placeholder="Withdrawal amount ($)" value={withdrawal} onChange={(e) => setWithdrawal(e.target.value)} />
            <button type="button" className={secondary} onClick={() => run(`accounts/${a.id}/proposals`, { trigger: "cash_flow", withdrawal_amount: withdrawal })}>Propose for a withdrawal</button>
          </div>
        ) : null}
        {c.end ? (
          <div className="mt-3 flex flex-wrap items-center gap-2 text-sm">
            <input className={input} placeholder="Reason to leave the model" value={reason} onChange={(e) => setReason(e.target.value)} />
            <button type="button" className={secondary} onClick={() => run(`accounts/${a.id}/model/end`, { reason })}>Leave the model</button>
          </div>
        ) : null}
        {mode && (c.join || c.changeModel || c.changeSettings) ? (
          <div className="mt-4"><SettingsForm envelope={env} mode={mode} onDone={reload} /></div>
        ) : null}
      </div>

      {pending ? (
        <div className={`${card} p-6 text-sm`}>
          <h2 className="text-lg font-semibold text-navy">Awaiting approval</h2>
          <p className="mt-1">{labelOf(v.changes, pending.change_kind)} — {pending.model_name}. A different holder of approve_proposals must decide.</p>
          {pending.proposed_fee?.schedule ? <p className="mt-1">Fee: {pending.proposed_fee.schedule} on this account</p> : null}
          {(pending.proposed_fee?.exclusions || []).map((e) => <p key={e.asset_id} className="text-text-muted">Not billed while held: {e.label}</p>)}
          {c.approveChange || c.rejectChange ? (
            <div className="mt-3 flex flex-wrap items-center gap-2">
              <input className={input} placeholder="Note (required to reject)" value={note} onChange={(e) => setNote(e.target.value)} />
              {c.approveChange ? <button type="button" className={primary} onClick={() => run(`accounts/${a.id}/model/changes/${pending.id}/approve`, { note: note || undefined })}>Approve</button> : null}
              {c.rejectChange ? <button type="button" className={secondary} onClick={() => run(`accounts/${a.id}/model/changes/${pending.id}/reject`, { note })}>Reject</button> : null}
            </div>
          ) : null}
        </div>
      ) : null}

      <div className={`${card} p-6`}>
        <h2 className="text-lg font-semibold text-navy">Transition plan</h2>
        <div className="mt-2"><PlanView plan={env?.transition_plan} v={v} /></div>
      </div>

      <div className={`${card} p-6`}>
        <h2 className="text-lg font-semibold text-navy">Proposals</h2>
        {proposals.length ? (
          <ul className="mt-2 space-y-1 text-sm">{proposals.map((p) => (
            <li key={p.id}><a className="text-navy hover:underline" href={`/portfolio/proposals/${p.id}`}>
              {String(p.created_at).slice(0, 10)} — {labelOf(v.triggers, p.trigger)} — {labelOf(v.statuses, p.status)}</a>
              {p.advice_only ? <span className="ml-2 text-xs text-text-muted">advice only</span> : null}</li>
          ))}</ul>
        ) : <p className="mt-2 text-sm text-text-muted">No proposals yet.</p>}
      </div>
      {msg ? <p className="text-sm text-[#9B2335]">{msg}</p> : null}
    </div>
  );
}
