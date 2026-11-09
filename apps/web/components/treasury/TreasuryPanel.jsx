"use client";

/**
 * TreasuryPanel — Household → Treasury (CASH01): the coverage card (months, covered through, next gap), the
 * per-entity coverage table with flags, the obligations calendar and list (filter by entity / certainty / source;
 * derived items name their source and why), the lines of credit shown SEPARATELY (never counted), what is not
 * estimated, and — for a writer only — add / edit / end a manual obligation and record a capital-call notice. CASH02:
 * the cash policy (the three sleeves, the questionnaire, maker-checker approval) renders in PolicyPanel, inside this
 * tab; the minimum coverage is edited there (it moved into the policy). A member envelope renders read-only with the estimate labels from the disclosure library. Every control
 * renders only from lib/treasury.mjs (fails closed). Light / cream; read-only on money.
 */

import { useState } from "react";
import {
  certaintyOptions, controls, coveredText, estimateLabel, filterObligations, flagLabels, gapText, labelOf, money,
  monthsText, noticeBody, obligationBody, originText, payerOptions, sourceOptions, taxLabel, vocab,
} from "@/lib/treasury.mjs";
import PolicyPanel from "@/components/treasury/PolicyPanel";
import LadderPanel from "@/components/treasury/LadderPanel";

const card = "rounded-[6px] border border-[#ece8dd] bg-white p-4";
const input = "rounded border border-[#E2E8F0] px-3 py-2";
const primary = "rounded bg-navy px-3 py-1 text-white disabled:opacity-50";
const secondary = "rounded border border-navy px-3 py-1 text-navy disabled:opacity-50";

async function call(path, method, body) {
  const res = await fetch(`/api/cash/${path}`, {
    method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || data.error || `Request failed (${res.status})`);
  return data;
}

export function CoverageCard({ env }) {
  const c = env?.coverage;
  if (!c) return null;
  const est = estimateLabel(env);
  return (
    <section className={card} aria-label="Cash coverage">
      <h2 className="text-lg font-semibold text-navy" style={{ fontFamily: "'Spectral', Georgia, serif" }}>Cash coverage</h2>
      <div className="mt-2 grid grid-cols-1 gap-3 sm:grid-cols-3">
        <div><p className="text-sm text-text-muted">Months of coverage</p><p className="text-2xl text-navy">{monthsText(c)}</p>
          <p className="text-xs text-text-muted">Minimum {c.min_months} months</p></div>
        <div><p className="text-sm text-text-muted">Liquid balance</p><p className="text-2xl text-navy">{money(c.liquid)}</p>
          <p className="text-xs text-text-muted">{c.liquid_basis}</p></div>
        <div><p className="text-sm text-text-muted">{coveredText(c)}</p><p className="text-sm text-[#334155]">{gapText(c)}</p></div>
      </div>
      {c.flags?.length ? <p className="mt-2 text-sm text-[#9B2335]">{flagLabels(env, c.flags).join(" · ")}</p> : null}
      <p className="mt-2 text-xs text-text-muted">As of {c.as_of}; average monthly outflow {money(c.monthly_outflow)} (next 12 months).</p>
      {est ? <p className="mt-2 text-xs text-text-muted" data-label="cash.coverage_estimate">{est}</p> : null}
    </section>
  );
}

export function EntityTable({ env }) {
  const rows = Array.isArray(env?.entities) ? env.entities : [];
  if (!rows.length) return <p className="text-sm text-text-muted">No entity of this household has cash or obligations yet.</p>;
  return (
    <table className="w-full text-sm" aria-label="Coverage by entity">
      <thead className="text-left text-text-muted"><tr><th className="py-1">Entity</th><th>Liquid</th><th>Months</th>
        <th>Covered through</th><th>Next gap</th><th>Flags</th></tr></thead>
      <tbody>{rows.map((r) => (
        <tr key={r.entity_id} className="border-t border-[#ece8dd] align-top">
          <td className="py-1">{r.display_name}</td>
          <td>{r.has_cash ? money(r.liquid) : "No cash"}</td>
          <td>{monthsText(r)} <span className="block text-xs text-text-muted">min {r.min_months}{r.min_basis ? ` (${r.min_basis})` : ""}</span></td>
          <td>{r.covered_beyond_horizon ? `beyond ${r.covered_through}` : r.covered_through}</td>
          <td>{r.next_gap ? `${r.next_gap.name}, ~${money(r.next_gap.amount)}, ${r.next_gap.date}` : "—"}</td>
          <td className="text-[#9B2335]">{flagLabels(env, r.flags).join(" · ")}</td>
        </tr>))}
      </tbody>
    </table>
  );
}

export function ObligationsCalendar({ env }) {
  const months = Array.isArray(env?.calendar) ? env.calendar : [];
  if (!months.length) return null;
  return (
    <div className="grid grid-cols-2 gap-2 sm:grid-cols-4 lg:grid-cols-6" aria-label="Obligations calendar">
      {months.map((m) => (
        <div key={m.month} className="rounded-[6px] border border-[#ece8dd] bg-[#FAF9F6] p-2">
          <p className="text-xs text-text-muted">{m.label}</p>
          <p className="text-sm text-navy">{m.count ? money(m.total) : "—"}</p>
          <p className="text-xs text-text-muted">{m.count} {m.count === 1 ? "item" : "items"}</p>
        </div>))}
    </div>
  );
}

export function ObligationList({ env, filters, onEdit, onEnd }) {
  const v = vocab(env);
  const rows = filterObligations(env, filters || {});
  const c = controls(env);
  const tax = taxLabel(env);
  if (!rows.length) return <p className="text-sm text-text-muted">No obligations in the horizon.</p>;
  return (
    <table className="w-full text-sm" aria-label="Obligations">
      <thead className="text-left text-text-muted"><tr><th className="py-1">Date</th><th>Obligation</th><th>Payer</th>
        <th>Amount</th><th>Certainty</th><th>Source</th>{c.editObligation ? <th /> : null}</tr></thead>
      <tbody>{rows.map((o) => (
        <tr key={o.key} className={`border-t border-[#ece8dd] align-top ${o.counted ? "" : "text-text-muted line-through"}`}>
          <td className="py-1">{o.date}</td>
          <td>{o.name}<span className="block text-xs text-text-muted">{originText(o)}</span>
            {o.source_type === "tax_estimate" && tax ? <span className="block text-xs text-text-muted" data-label="tax.estimate_label">{tax}</span> : null}</td>
          <td>{o.payer_name}</td>
          <td>{money(o.amount)}</td>
          <td>{labelOf(v.certainties, o.certainty)}</td>
          <td>{o.source_label}{o.derived ? <span className="block text-xs text-text-muted">derived</span> : null}</td>
          {c.editObligation ? <td>{!o.derived && o.obligation_id ? (
            <div className="flex gap-2"><button type="button" className={secondary} onClick={() => onEdit?.(o)}>Edit</button>
              <button type="button" className={secondary} onClick={() => onEnd?.(o)}>End</button></div>) : null}</td> : null}
        </tr>))}
      </tbody>
    </table>
  );
}

export function NotEstimated({ env }) {
  const rows = Array.isArray(env?.not_estimated) ? env.not_estimated : [];
  const credit = Array.isArray(env?.lines_of_credit) ? env.lines_of_credit : [];
  return (
    <div className="space-y-2 text-sm">
      {credit.length ? (
        <div aria-label="Lines of credit"><p className="font-semibold text-navy">Lines of credit (shown separately — not counted as liquid)</p>
          <ul>{credit.map((l) => <li key={l.liability_id}>{l.name} ({l.payer_name}): drawn {money(l.drawn)}; undrawn {l.undrawn ? money(l.undrawn) : "—"}<span className="block text-xs text-text-muted">{l.note}</span></li>)}</ul></div>) : null}
      {rows.length ? (
        <div aria-label="Not estimated"><p className="font-semibold text-navy">Not estimated</p>
          <ul>{rows.map((x, i) => <li key={`${x.source_ref_id}-${i}`}>{x.name} ({x.payer_name}): {x.text}</li>)}</ul></div>) : null}
    </div>
  );
}

function ObligationForm({ env, initial, onDone, onCancel, mode }) {
  const [f, setF] = useState(initial || { certainty: "estimated" });
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value });
  const hid = env.household.id;
  const submit = async () => {
    setBusy(true); setErr(null);
    try {
      if (mode === "notice") await call(`households/${hid}/notices`, "POST", noticeBody(f));
      else if (mode === "edit") {
        const b = obligationBody(f); delete b.entity_id;
        await call(`obligations/${f.obligation_id}/revise`, "POST", { changes: b, reason: f.reason || null });
      } else await call(`households/${hid}/obligations`, "POST", obligationBody(f));
      onDone?.();
    } catch (e) { setErr(e.message); } finally { setBusy(false); }
  };
  return (
    <div className={`${card} mt-2 grid grid-cols-1 gap-2 sm:grid-cols-3`}>
      {mode !== "edit" ? <select className={input} value={f.entity_id || ""} onChange={set("entity_id")}><option value="">Payer…</option>
        {payerOptions(env).map((p) => <option key={p.entity_id} value={p.entity_id}>{p.display_name}</option>)}</select> : null}
      {mode !== "notice" ? <select className={input} value={f.source_type || ""} onChange={set("source_type")}><option value="">Kind…</option>
        {sourceOptions(env).map((s) => <option key={s.key} value={s.key}>{s.label}</option>)}</select> : null}
      <input className={input} placeholder="Name" value={f.name || ""} onChange={set("name")} />
      <input className={input} placeholder="Amount, e.g. 125000.00" value={f.amount || ""} onChange={set("amount")} />
      <input className={input} type="date" value={f.due_date || ""} onChange={set("due_date")} />
      {mode !== "notice" ? <>
        <input className={input} placeholder="Recurrence, e.g. FREQ=MONTHLY" value={f.rrule || ""} onChange={set("rrule")} />
        <input className={input} type="date" value={f.end_date || ""} onChange={set("end_date")} />
        <select className={input} value={f.certainty || ""} onChange={set("certainty")}>
          {certaintyOptions(env).map((c) => <option key={c.key} value={c.key}>{c.label}</option>)}</select></> : null}
      <input className={input} placeholder="Notes" value={f.notes || ""} onChange={set("notes")} />
      <div className="flex gap-2"><button type="button" className={primary} disabled={busy} onClick={submit}>Save</button>
        <button type="button" className={secondary} onClick={onCancel}>Cancel</button></div>
      {err ? <p className="text-sm text-[#9B2335]">{err}</p> : null}
    </div>
  );
}

export default function TreasuryPanel({ initialEnvelope }) {
  const [env, setEnv] = useState(initialEnvelope);
  const [filters, setFilters] = useState({ entity: "", certainty: "", source: "" });
  const [form, setForm] = useState(null);
  const [msg, setMsg] = useState(null);
  if (!env || !env.household) {
    return <div className={`${card} text-center text-sm text-text-muted`}>Treasury is open to staff with portfolio access and to the family itself.</div>;
  }
  const c = controls(env);
  const v = vocab(env);
  const reload = async () => {
    const res = await fetch(`/api/cash/households/${env.household.id}/treasury`);
    if (res.ok) setEnv(await res.json());
    setForm(null);
  };
  const endIt = async (o) => {
    try { await call(`obligations/${o.obligation_id}/end`, "POST", { reason: "ended on Treasury" }); await reload(); } catch (e) { setMsg(e.message); }
  };
  return (
    <div className="space-y-4">
      <CoverageCard env={env} />
      <PolicyPanel env={env} onReload={reload} />
      <LadderPanel env={env} onReload={reload} />
      <section className={card}><h3 className="mb-2 font-semibold text-navy">By entity</h3><EntityTable env={env} /></section>
      <section className={card}>
        <div className="mb-2 flex flex-wrap items-center gap-2">
          <h3 className="mr-auto font-semibold text-navy">Obligations</h3>
          <select className={input} value={filters.entity} onChange={(e) => setFilters({ ...filters, entity: e.target.value })}><option value="">All entities</option>
            {(env.entities || []).map((r) => <option key={r.entity_id} value={r.entity_id}>{r.display_name}</option>)}</select>
          <select className={input} value={filters.certainty} onChange={(e) => setFilters({ ...filters, certainty: e.target.value })}><option value="">Any certainty</option>
            {(v.certainties || []).map((x) => <option key={x.key} value={x.key}>{x.label}</option>)}</select>
          <select className={input} value={filters.source} onChange={(e) => setFilters({ ...filters, source: e.target.value })}><option value="">Any source</option>
            {(v.source_types || []).map((x) => <option key={x.key} value={x.key}>{x.label}</option>)}</select>
          {c.addObligation ? <button type="button" className={primary} onClick={() => setForm({ mode: "add" })}>Add obligation</button> : null}
          {c.recordNotice ? <button type="button" className={secondary} onClick={() => setForm({ mode: "notice" })}>Record capital-call notice</button> : null}
        </div>
        {form && (c.addObligation || c.recordNotice) ? <ObligationForm env={env} mode={form.mode} initial={form.initial} onDone={reload} onCancel={() => setForm(null)} /> : null}
        <ObligationsCalendar env={env} />
        <div className="mt-3"><ObligationList env={env} filters={filters} onEnd={endIt}
          onEdit={(o) => { const s = (env.stored || []).find((r) => r.id === o.obligation_id); if (s) setForm({ mode: "edit", initial: { ...s, obligation_id: s.id } }); }} /></div>
      </section>
      <section className={card}><NotEstimated env={env} /></section>
      {msg ? <p className="text-sm text-[#9B2335]">{msg}</p> : null}
    </div>
  );
}
