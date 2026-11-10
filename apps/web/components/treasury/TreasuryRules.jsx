"use client";

/**
 * TreasuryRules — Household → Treasury → Rules (CASH06, stage A), inside the EXISTING Treasury tab (no second tab):
 * standing cash rules that DRAFT payment instructions for a person's approval. The list (status, last run, next run,
 * holds), the editor per rule type (distribute · maintain a target · recurring · redirect incoming — destinations with
 * % or $, the condition, the frequency, the caps) with a PREVIEW of what it would draft today, the detail with its run
 * HISTORY (engine runs → the instructions they drafted, each a link into Payments, or the hold and why), and pause /
 * ask to resume / retire. Approve / Return appear only where the server lists them (the S30 floor — never the owner or
 * a maker). A member reads their family's ACTIVE rules read-only with the cash.rules text. Every decision comes from
 * lib/cashRules.mjs, which fails closed; every read goes through the Next.js proxy (/api/cash/households/{id}/rules…).
 * Light / cream.
 *
 * ``initial`` ({list, options, detail, preview}) renders without fetching (render tests).
 */

import { useCallback, useEffect, useState } from "react";
import {
  actionRequest, canWrite, destinationChoices, detailOf, fieldsFor, historyRows, isMember, labelText, newOffer,
  previewOf, railChoices, readable, ruleBody, ruleRows, sourceChoices, vocab,
} from "@/lib/cashRules.mjs";

const card = "rounded-[6px] border border-[#ece8dd] bg-white p-4";
const serif = { fontFamily: "'Spectral', Georgia, serif" };
const input = "rounded border border-[#E2E8F0] px-2 py-1 text-sm";
const btn = "rounded border border-[#C5A880] bg-[#F5F1EB] px-3 py-1 text-sm text-navy";

async function call(path, { method = "GET", body } = {}) {
  const res = await fetch(path, { method, headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const d = data.detail;
    throw new Error((d && d.reasons ? d.reasons.join(" · ") : d?.message) || data.error || d || `Request failed (${res.status})`);
  }
  return data;
}

function Disclosures({ data }) {
  const rules = labelText(data, "cash.rules");
  const pay = labelText(data, "cash.payments");
  return (
    <>
      {rules ? <p className="text-xs text-text-muted" data-label="cash.rules">{rules}</p> : null}
      {pay && !isMember(data) ? <p className="text-xs text-text-muted" data-label="cash.payments">{pay}</p> : null}
    </>
  );
}

export function RuleList({ data, onOpen, onNew }) {
  if (!readable(data)) return null;
  const rows = ruleRows(data);
  const member = isMember(data);
  return (
    <section id="treasury-rules" className="space-y-2" aria-label="Rules">
      <div className="flex flex-wrap items-baseline gap-2">
        <h3 className="mr-auto text-lg font-semibold text-navy" style={serif}>Cash rules</h3>
        {newOffer(data) ? <button type="button" className={btn} onClick={onNew}>New rule</button> : null}
      </div>
      <Disclosures data={data} />
      {rows.length === 0 ? <p className={`${card} text-sm text-text-muted`} data-empty="rules">No cash rules.</p> : (
        <table className={`${card} w-full text-sm`}>
          <thead className="text-left text-text-muted"><tr><th className="py-1">Rule</th><th>From</th><th>To</th><th>When</th>
            <th>Status</th>{member ? null : <><th>Last run</th><th>Next run</th><th>Holds</th></>}</tr></thead>
          <tbody>{rows.map((r) => (
            <tr key={r.ruleId} className={`border-t border-[#ece8dd] align-top ${member ? "" : "cursor-pointer"}`}
              onClick={member ? undefined : () => onOpen?.(r.id)} data-rule={r.ruleId}>
              <td className="py-1">{r.name}<span className="block text-xs text-text-muted">{r.type} · {r.rail} · cap {r.cap}</span></td>
              <td>{r.from}</td><td>{r.to}</td><td>{r.schedule}</td>
              <td>{r.status}{r.pending ? <span className="block text-xs text-text-muted">{r.pending}</span> : null}</td>
              {member ? null : <><td>{r.lastRun || "—"}</td><td>{r.nextRun || "—"}</td>
                <td data-holds={r.holds}>{r.holds ? `${r.holds} held` : "—"}</td></>}
            </tr>))}
          </tbody>
        </table>)}
    </section>
  );
}

const EMPTY = { name: "", rule_type: "", source: "", basis: "", frequency: "", day_of_week: "1", day_of_month: "1",
  rail: "", cap_per_firing: "", max_per_day: "1", condition: "", target_amount: "", supporting: "",
  destinations: [{ account_id: "", value: "" }] };

export function RuleEditor({ options, initialForm = null, preview = null, onSave, onPreview, onCancel, busy = false }) {
  const [form, setForm] = useState({ ...EMPTY, ...(initialForm || {}) });
  if (!canWrite(options)) return null;
  const src = sourceChoices(options);
  const same = destinationChoices(options, form.source);
  const rails = railChoices(options, form.source);
  const fl = fieldsFor(form.rule_type, form.basis);
  const body = ruleBody(options, form);
  const pv = previewOf(preview);
  const set = (k) => (e) => setForm((c) => ({ ...c, [k]: e.target.value,
    ...(k === "source" ? { rail: "", supporting: "", destinations: [{ account_id: "", value: "" }] } : {}) }));
  const setDest = (i, k) => (e) => setForm((c) => ({ ...c, destinations: c.destinations.map((d, j) => (j === i ? { ...d, [k]: e.target.value } : d)) }));
  const ceil = options.ceilings || {};
  return (
    <section className={`${card} space-y-2`} aria-label="Rule editor">
      <h4 className="font-semibold text-navy">Cash rule — between the family&apos;s own accounts; it prepares instructions for approval</h4>
      <label className="block text-sm">Name <input className={`${input} ml-2 w-2/3`} aria-label="Name" value={form.name} onChange={set("name")} /></label>
      <label className="block text-sm">Type
        <select className={`${input} ml-2`} aria-label="Rule type" value={form.rule_type} onChange={set("rule_type")}>
          <option value="">Choose a rule type</option>
          {vocab(options, "rule_types").map((t) => <option key={t.key} value={t.key}>{t.label}</option>)}
        </select></label>
      <label className="block text-sm">Source account
        <select className={`${input} ml-2`} aria-label="Source account" value={form.source} onChange={set("source")}>
          <option value="">Choose an account</option>
          {src.eligible.map((s) => <option key={s.key} value={s.key}>{s.label}</option>)}
        </select></label>
      {src.refused.length ? (
        <ul className="text-xs text-text-muted" aria-label="Accounts you cannot make a rule on">
          {src.refused.map((s) => <li key={s.key} data-refused={s.key}>{s.label}: {s.reasons.join(" · ")}</li>)}
        </ul>) : null}
      {fl && fl.basis ? (
        <label className="block text-sm">How much
          <select className={`${input} ml-2`} aria-label="Basis" value={form.basis} onChange={set("basis")}>
            <option value="">Choose</option>
            {vocab(options, "bases").map((b) => <option key={b.key} value={b.key}>{b.label}</option>)}
          </select></label>) : null}
      {fl && fl.target ? (
        <div className="space-y-1" aria-label="Target">
          <label className="block text-sm">Condition
            <select className={`${input} ml-2`} aria-label="Condition" value={form.condition} onChange={set("condition")}>
              <option value="">Choose</option>
              {vocab(options, "conditions").map((c) => <option key={c.key} value={c.key}>{c.label}</option>)}
            </select></label>
          <label className="block text-sm">Target balance (USD) <input className={`${input} ml-2`} aria-label="Target balance" inputMode="decimal" value={form.target_amount} onChange={set("target_amount")} /></label>
          <label className="block text-sm">Supporting account
            <select className={`${input} ml-2`} aria-label="Supporting account" value={form.supporting} onChange={set("supporting")}>
              <option value="">Choose a same-owner account</option>
              {same.map((d) => <option key={d.key} value={d.key}>{d.label}</option>)}
            </select></label>
        </div>) : null}
      {fl && fl.destinations !== "none" ? (
        <div className="space-y-1" aria-label="Destinations">
          {form.destinations.map((d, i) => (
            <div key={i} className="flex items-center gap-2 text-sm">
              <select className={input} aria-label={`Destination ${i + 1}`} value={d.account_id} onChange={setDest(i, "account_id")}>
                <option value="">Choose a same-owner account</option>
                {same.map((x) => <option key={x.key} value={x.key}>{x.label}</option>)}
              </select>
              <input className={`${input} w-28`} aria-label={`Destination ${i + 1} ${fl.amountKind === "pct" ? "percent" : "amount"}`}
                inputMode="decimal" value={d.value} onChange={setDest(i, "value")} />
              <span className="text-text-muted">{fl.amountKind === "pct" ? "%" : "USD"}</span>
            </div>))}
          {fl.destinations === "many" && form.destinations.length < (options.max_destinations || 5) ? (
            <button type="button" className="text-sm text-navy underline"
              onClick={() => setForm((c) => ({ ...c, destinations: [...c.destinations, { account_id: "", value: "" }] }))}>Add a destination</button>) : null}
        </div>) : null}
      {fl && fl.frequency ? (
        <label className="block text-sm">How often
          <select className={`${input} ml-2`} aria-label="Frequency" value={form.frequency} onChange={set("frequency")}>
            <option value="">Choose</option>
            {vocab(options, "frequencies").filter((f) => f.key !== "on_incoming").map((f) => <option key={f.key} value={f.key}>{f.label}</option>)}
          </select>
          {form.frequency === "weekly" ? (
            <select className={`${input} ml-2`} aria-label="Weekday" value={form.day_of_week} onChange={set("day_of_week")}>
              {vocab(options, "weekdays").map((w) => <option key={w.key} value={String(w.key)}>{w.label}</option>)}
            </select>) : null}
          {form.frequency === "monthly" ? <input className={`${input} ml-2 w-16`} aria-label="Day of month" value={form.day_of_month} onChange={set("day_of_month")} /> : null}
          <span className="ml-2 text-xs text-text-muted">Runs at {options.fire_time}.</span></label>)
        : fl ? <p className="text-sm text-text-muted">Runs on each incoming credit to the source account.</p> : null}
      <label className="block text-sm">Rail
        <select className={`${input} ml-2`} aria-label="Rail" value={form.rail} onChange={set("rail")}>
          <option value="">Choose a rail</option>
          {rails.map((r) => <option key={r.key} value={r.key}>{r.label}</option>)}
        </select></label>
      <label className="block text-sm">Cap per run (USD) <input className={`${input} ml-2`} aria-label="Cap per run" inputMode="decimal" value={form.cap_per_firing} onChange={set("cap_per_firing")} />
        <span className="ml-2 text-xs text-text-muted">At most {ceil.max_amount_per_firing} (your firm&apos;s ceiling).</span></label>
      <label className="block text-sm">Instructions per day <input className={`${input} ml-2 w-16`} aria-label="Instructions per day" value={form.max_per_day} onChange={set("max_per_day")} />
        <span className="ml-2 text-xs text-text-muted">At most {ceil.max_instructions_per_day}.</span></label>
      <Disclosures data={options} />
      {!body.ok ? <p className="text-xs text-text-muted" data-errors="rule">{body.errors.join(" · ")}</p> : null}
      {pv ? (
        <div className="rounded-[6px] border border-[#ece8dd] bg-[#FAF9F6] p-3 text-sm" aria-label="Preview">
          <p className="font-semibold text-navy">If it ran today: {pv.outcome}{pv.drafts.length ? ` — ${pv.total}` : ""}</p>
          {pv.exampleCredit ? <p className="text-xs text-text-muted">For an example credit of {pv.exampleCredit}.</p> : null}
          <ul>{pv.drafts.map((d, i) => <li key={i} data-draft={i}>Would draft {d.amount} from {d.from} to {d.to} — {d.reason}</li>)}</ul>
          <ul>{pv.holds.map((h, i) => <li key={i} className="text-[#9B2335]" data-hold={i}>{h.kind}: {h.reason}</li>)}</ul>
          {pv.skipped ? <p className="text-text-muted">{pv.skipped}</p> : null}
          {pv.notes.map((n, i) => <p key={i} className="text-xs text-text-muted">{n}</p>)}
          {pv.refusals.length ? <p className="text-xs text-[#9B2335]">Would be refused: {pv.refusals.join(" · ")}</p> : null}
          <p className="text-xs text-text-muted">A preview drafts nothing and sends nothing.</p>
        </div>) : null}
      <div className="flex gap-2">
        <button type="button" className={btn} disabled={!body.ok || busy} onClick={() => body.ok && onPreview?.(body.body)}>Preview today</button>
        <button type="button" className={btn} disabled={!body.ok || busy} onClick={() => body.ok && onSave?.(body.body)}>Save draft</button>
        <button type="button" className="text-sm text-text-muted underline" onClick={onCancel}>Close</button>
      </div>
    </section>
  );
}

export function RuleHistory({ data, householdId }) {
  const rows = historyRows(data, householdId);
  if (!readable(data) || isMember(data)) return null;
  return (
    <div aria-label="Run history" className="space-y-1">
      <h5 className="text-sm font-semibold text-navy">Run history</h5>
      {rows.length === 0 ? <p className="text-sm text-text-muted" data-empty="history">The rule has not run.</p> : (
        <table className="w-full text-sm">
          <thead className="text-left text-text-muted"><tr><th>Day</th><th>Trigger</th><th>Run</th><th>Outcome</th><th>Instructions</th></tr></thead>
          <tbody>{rows.map((h) => (
            <tr key={h.id} className="border-t border-[#ece8dd] align-top" data-firing={h.period}>
              <td className="py-1">{h.on}</td><td>{h.trigger}</td><td>{h.run || "—"}</td>
              <td>{h.outcome}{h.reason ? <span className="block text-xs text-text-muted">{h.reason}</span> : null}</td>
              <td>{h.instructions.length ? h.instructions.map((p) => <a key={p.id} className="block text-navy underline" href={p.href}>{p.label}</a>) : "—"}</td>
            </tr>))}
          </tbody>
        </table>)}
    </div>
  );
}

export function RuleDetail({ data, householdId, onAction, onEdit, onClose, busy = false }) {
  const [fields, setFields] = useState({});
  const d = detailOf(data);
  if (!d) return null;
  const has = (k) => d.actions.some((a) => a.key === k);
  return (
    <section className={`${card} space-y-2`} aria-label="Cash rule">
      <div className="flex items-baseline gap-2">
        <h4 className="mr-auto font-semibold text-navy">{d.name} · v{d.version} · {d.status}</h4>
        <button type="button" className="text-sm text-text-muted underline" onClick={onClose}>Close</button>
      </div>
      <p className="text-sm">{d.type}: from {d.from} → {d.to} · {d.schedule} · {d.rail} · cap {d.cap} a run, {d.perDay} a day</p>
      <p className="text-xs text-text-muted">Owner {d.owner || "—"} (the maker of every instruction it drafts — a different person approves each)
        {d.approvedBy ? ` · approved by ${d.approvedBy}` : ""}{d.pauseReason ? ` · paused: ${d.pauseReason}` : ""}</p>
      <Disclosures data={data} />
      <RuleHistory data={data} householdId={householdId} />
      {d.actions.length ? (
        <div className="space-y-2" aria-label="Actions">
          {has("pause") || has("retire") ? <label className="block text-sm">Reason <input className={`${input} ml-2 w-2/3`} aria-label="Reason" value={fields.reason || ""} onChange={(e) => setFields((c) => ({ ...c, reason: e.target.value }))} /></label> : null}
          {has("approve") ? <label className="block text-sm">Note <input className={`${input} ml-2 w-2/3`} aria-label="Note" value={fields.note || ""} onChange={(e) => setFields((c) => ({ ...c, note: e.target.value }))} /></label> : null}
          <div className="flex flex-wrap gap-2">
            {d.actions.map((a) => (a.key === "edit" || a.key === "new_version"
              ? <button key={a.key} type="button" className={btn} disabled={busy} onClick={() => onEdit?.(a.key)}>{a.label}</button>
              : <button key={a.key} type="button" className={btn} disabled={busy} onClick={() => onAction?.(...actionRequest(a.key, fields))}>{a.label}</button>))}
          </div>
        </div>) : null}
    </section>
  );
}

/** The form an edit starts from: the version's own terms. */
function formOf(v) {
  if (!v) return null;
  const pct = (v.destinations || []).some((d) => d.pct !== undefined);
  return { name: v.name, rule_type: v.rule_type, source: v.source?.id || "", basis: v.basis || "", frequency: v.frequency,
    day_of_week: String(v.day_of_week || 1), day_of_month: String(v.day_of_month || 1), rail: v.rail,
    cap_per_firing: v.cap_per_firing, max_per_day: String(v.max_per_day), condition: v.condition || "",
    target_amount: v.target_amount || "", supporting: v.supporting?.id || "",
    destinations: (v.destinations || []).map((d) => ({ account_id: d.id, value: pct ? d.pct : d.amount })) };
}

export default function TreasuryRules({ householdId, initial = null }) {
  const [data, setData] = useState(initial || {});
  const [err, setErr] = useState(null);
  const [busy, setBusy] = useState(false);
  const [editing, setEditing] = useState(null);   // null | {mode: "new" | "edit" | "new_version", form}
  const base = householdId ? `/api/cash/households/${encodeURIComponent(householdId)}/rules` : null;

  const loadList = useCallback(() => {
    if (!base || initial) return undefined;
    let alive = true;
    call(base).then((d) => { if (alive) setData((c) => ({ ...c, list: d })); }).catch((e) => { if (alive) setErr(e.message); });
    return () => { alive = false; };
  }, [base, initial]);
  useEffect(() => loadList(), [loadList]);

  if (!base || !readable(data.list)) return null;
  const run = (p) => { setBusy(true); setErr(null); return p.catch((e) => setErr(e.message)).finally(() => setBusy(false)); };
  const open = (id) => call(`${base}/${encodeURIComponent(id)}`).then((d) => setData((c) => ({ ...c, detail: d })))
    .catch((e) => setErr(e.message));
  const vid = data.detail?.rule?.version?.id;
  const startEdit = (mode) => call(`${base}/options`).then((o) => {
    setData((c) => ({ ...c, options: o, preview: null }));
    setEditing({ mode, form: mode === "new" ? null : formOf(data.detail?.rule?.version) });
  }).catch((e) => setErr(e.message));
  const save = (body) => run((editing?.mode === "new" ? call(base, { method: "POST", body })
    : call(`${base}/${encodeURIComponent(vid)}/${editing.mode}`, { method: "POST", body: { changes: body } }))
    .then((d) => { setEditing(null); setData((c) => ({ ...c, options: null, detail: { ...c.list, rule: d.rule } })); loadList(); }));
  const act = (key, body) => run(call(`${base}/${encodeURIComponent(vid)}/${key}`, { method: "POST", body })
    .then((d) => { setData((c) => ({ ...c, detail: { ...c.detail, rule: d.rule } })); loadList(); }));
  return (
    <div className="space-y-3">
      <RuleList data={data.list} onOpen={open} onNew={() => startEdit("new")} />
      {editing && data.options && canWrite(data.list) ? (
        <RuleEditor options={data.options} initialForm={editing.form} preview={data.preview} busy={busy}
          onCancel={() => { setEditing(null); setData((c) => ({ ...c, options: null, preview: null })); }}
          onPreview={(body) => run(call(`${base}/preview`, { method: "POST", body }).then((p) => setData((c) => ({ ...c, preview: p }))))}
          onSave={save} />) : null}
      {data.detail ? (
        <RuleDetail data={data.detail} householdId={householdId} busy={busy} onAction={act} onEdit={startEdit}
          onClose={() => setData((c) => ({ ...c, detail: null }))} />) : null}
      {err ? <p className="text-sm text-[#9B2335]" role="alert">{err}</p> : null}
      {isMember(data.list) ? null : <p className="text-xs text-text-muted">A rule never approves, sends or moves money. Each instruction it drafts waits in Payments (and the task inbox) for a second person — never the rule&apos;s owner.</p>}
    </div>
  );
}
