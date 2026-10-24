"use client";

/**
 * Profiles — Portfolio → Tax → Profiles (REP6): member tax profiles per
 * household and year (adviser-entered rates with an as-of and a source,
 * approved by a second person) and loss carryforwards from the prior return.
 * Combined rates shown are the server's (never stored, never a default).
 * Enter / Approve / Reject render only from the envelope (fail closed).
 */

import { useState } from "react";
import { labelOf, money, pageControls, pct, profileBody, subjectName, vocab } from "@/lib/tax.mjs";
import { Decide } from "@/components/tax/Substitutions";
import { card, input, post, primary } from "@/components/tax/common";

function ProfileForm({ envelope, subjects, onDone }) {
  const v = vocab(envelope);
  const [f, setF] = useState({ subjectType: "", subjectId: "", taxYear: String(new Date().getFullYear()), filingStatus: "", niitApplies: false });
  const [msg, setMsg] = useState(null);
  const set = (k) => (e) => setF({ ...f, [k]: e.target.type === "checkbox" ? e.target.checked : e.target.value });
  async function submit() {
    setMsg(null);
    try { await post("tax/profiles", profileBody(f)); setMsg("Entered — a second person approves it."); if (onDone) await onDone(); }
    catch (e) { setMsg(e.message); }
  }
  return (
    <div className="mt-3 grid grid-cols-2 gap-2 border-t border-[#ece8dd] pt-3">
      <p className="col-span-2 font-medium">Enter a tax profile (rates in percent, from the adviser)</p>
      <select className={input} value={`${f.subjectType}:${f.subjectId}`} onChange={(e) => { const [t, i] = e.target.value.split(":"); setF({ ...f, subjectType: t, subjectId: i }); }}>
        <option value=":">Household or member…</option>
        {(subjects || []).map((s) => <option key={`${s.subject_type}:${s.subject_id}`} value={`${s.subject_type}:${s.subject_id}`}>{s.name} ({labelOf(v.subject_types, s.subject_type)})</option>)}
      </select>
      <input className={input} placeholder="Tax year" value={f.taxYear} onChange={set("taxYear")} />
      <select className={input} value={f.filingStatus} onChange={set("filingStatus")}>
        <option value="">Filing status…</option>
        {(v.filing_statuses || []).map((s) => <option key={s.key} value={s.key}>{s.label}</option>)}
      </select>
      <label className="flex items-center gap-2"><input type="checkbox" checked={f.niitApplies} onChange={set("niitApplies")} /> Net investment income tax (3.8%) applies</label>
      <input className={input} placeholder="Federal ordinary / short-term %" onChange={set("federalOrdinaryPct")} />
      <input className={input} placeholder="Federal long-term %" onChange={set("federalLtPct")} />
      <input className={input} placeholder="State (code)" onChange={set("stateCode")} />
      <input className={input} placeholder="State %" onChange={set("statePct")} />
      <input className={input} placeholder="State long-term % (blank = ordinary)" onChange={set("stateLtPct")} />
      <input className={input} placeholder="City" onChange={set("city")} />
      <input className={input} placeholder="City %" onChange={set("cityPct")} />
      <input className={input} placeholder="City long-term % (blank = ordinary)" onChange={set("cityLtPct")} />
      <input className={input} type="date" onChange={set("asOf")} />
      <input className={input} placeholder="Source note (where the rates come from)" onChange={set("sourceNote")} />
      <div className="col-span-2"><button type="button" className={primary} onClick={submit}>Send for approval</button>
        {msg ? <span className="ml-2 text-sm text-text-muted">{msg}</span> : null}</div>
    </div>
  );
}

function CarryforwardForm({ envelope, subjects, onDone }) {
  const v = vocab(envelope);
  const [f, setF] = useState({ subject: ":", taxYear: String(new Date().getFullYear()), st: "", lt: "", doc: "" });
  const [msg, setMsg] = useState(null);
  async function submit() {
    setMsg(null);
    const [t, i] = f.subject.split(":");
    try {
      await post("tax/carryforwards", { subject_type: t, subject_id: i, tax_year: Number.parseInt(f.taxYear, 10),
        st_amount: f.st || "0", lt_amount: f.lt || "0", source_document: f.doc });
      setMsg("Entered — a second person approves it.");
      if (onDone) await onDone();
    } catch (e) { setMsg(e.message); }
  }
  return (
    <div className="mt-3 grid grid-cols-2 gap-2 border-t border-[#ece8dd] pt-3">
      <p className="col-span-2 font-medium">Enter a loss carryforward (from the prior return)</p>
      <select className={input} value={f.subject} onChange={(e) => setF({ ...f, subject: e.target.value })}>
        <option value=":">Household or member…</option>
        {(subjects || []).map((s) => <option key={`${s.subject_type}:${s.subject_id}`} value={`${s.subject_type}:${s.subject_id}`}>{s.name} ({labelOf(v.subject_types, s.subject_type)})</option>)}
      </select>
      <input className={input} placeholder="Into tax year" value={f.taxYear} onChange={(e) => setF({ ...f, taxYear: e.target.value })} />
      <input className={input} placeholder="Short-term loss carried in ($)" onChange={(e) => setF({ ...f, st: e.target.value })} />
      <input className={input} placeholder="Long-term loss carried in ($)" onChange={(e) => setF({ ...f, lt: e.target.value })} />
      <input className={`${input} col-span-2`} placeholder="Source document (e.g. 2025 Schedule D, line 7)" onChange={(e) => setF({ ...f, doc: e.target.value })} />
      <div className="col-span-2"><button type="button" className={primary} onClick={submit}>Send for approval</button>
        {msg ? <span className="ml-2 text-sm text-text-muted">{msg}</span> : null}</div>
    </div>
  );
}

export default function Profiles({ envelope, subjects, onReload }) {
  const v = vocab(envelope);
  const c = pageControls(envelope);
  const rows = Array.isArray(envelope?.rows) ? envelope.rows : [];
  const cfs = Array.isArray(envelope?.carryforwards) ? envelope.carryforwards : [];
  return (
    <div className="space-y-5">
      <div className={`${card} p-6 text-sm`}>
        <h2 className="text-lg font-semibold text-navy">Tax profiles</h2>
        <p className="mt-1 text-text-muted">Rates come from the adviser with a date; nothing is ever assumed. No approved profile → every tax value reads “{v.no_profile_label || "not estimated — no tax profile"}”.</p>
        {rows.length ? rows.map((p) => (
          <div key={p.id} className="mt-3 border-t border-[#ece8dd] pt-2">
            <p><span className="font-medium text-navy">{subjectName(subjects, p.subject_type, p.subject_id)}</span> · {p.tax_year} · v{p.version_no} · {labelOf(v.tax_input_statuses, p.status)}{p.current ? "" : " (an earlier version)"} · {labelOf(v.filing_statuses, p.filing_status)}</p>
            <p>Federal {pct(p.federal_ordinary_rate, 3)} ordinary / {pct(p.federal_lt_rate, 3)} long-term · NIIT {p.niit_applies ? "applies" : "does not apply"} · state {p.state_code || "—"} {pct(p.state_rate, 3)}{p.state_lt_rate ? ` (LT ${pct(p.state_lt_rate, 3)})` : ""} · city {p.city || "—"} {pct(p.city_rate, 3)}{p.city_lt_rate ? ` (LT ${pct(p.city_lt_rate, 3)})` : ""}</p>
            <p className="text-text-muted">As of {p.as_of} · source: {p.source_note}</p>
            {p.combined?.estimated ? <p>Combined (estimate): short-term {pct(p.combined.st, 3)}, long-term {pct(p.combined.lt, 3)}</p>
              : <p className="text-text-muted">Combined: {p.combined?.label}</p>}
            <Decide envelope={envelope} row={p} base="tax/profiles" onDone={onReload} />
          </div>
        )) : <p className="mt-2 text-text-muted">No profile yet.</p>}
        {c.createProfile ? <ProfileForm envelope={envelope} subjects={subjects} onDone={onReload} /> : null}
      </div>
      <div className={`${card} p-6 text-sm`}>
        <h2 className="text-lg font-semibold text-navy">Loss carryforwards</h2>
        {cfs.length ? cfs.map((x) => (
          <div key={x.id} className="mt-3 border-t border-[#ece8dd] pt-2">
            <p><span className="font-medium text-navy">{subjectName(subjects, x.subject_type, x.subject_id)}</span> · into {x.tax_year} · v{x.version_no} · {labelOf(v.tax_input_statuses, x.status)}</p>
            <p>Short-term {money(x.st_amount)} · long-term {money(x.lt_amount)} · source: {x.source_document}</p>
            <Decide envelope={envelope} row={x} base="tax/carryforwards" onDone={onReload} />
          </div>
        )) : <p className="mt-2 text-text-muted">No carryforward yet.</p>}
        {c.createCarryforward ? <CarryforwardForm envelope={envelope} subjects={subjects} onDone={onReload} /> : null}
      </div>
    </div>
  );
}
