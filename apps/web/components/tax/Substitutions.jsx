"use client";

/**
 * Substitutions — Portfolio → Tax → Substitutions (REP6): the substitution
 * table, a versioned APPROVED compliance artifact. Sets (every version, its
 * candidates with reason and evidence) and denials (pairs that may never pair).
 * Propose / Seed render only from `pageControls(envelope)`; Approve / Reject /
 * Retire only from `rowControls(envelope, row)` (never for the maker — the
 * server leaves those actions out). Only APPROVED sets are used by the engine.
 */

import { useState } from "react";
import { labelOf, pageControls, rowControls, vocab } from "@/lib/tax.mjs";
import { SecurityPicker, card, input, post, primary, secondary } from "@/components/tax/common";

function Decide({ envelope, row, base, onDone }) {
  const [note, setNote] = useState("");
  const [msg, setMsg] = useState(null);
  const c = rowControls(envelope, row);
  if (!c.approve && !c.reject && !c.retire) return null;
  async function act(kind) {
    setMsg(null);
    try {
      await post(`${base}/${row.id}/${kind}`, kind === "retire" ? { reason: note } : { note: note || undefined });
      if (onDone) await onDone();
    } catch (e) { setMsg(e.message); }
  }
  return (
    <div className="mt-2 flex flex-wrap items-center gap-2">
      <input className={input} placeholder={c.retire ? "Reason (required to retire)" : "Note (required to reject)"} value={note} onChange={(e) => setNote(e.target.value)} />
      {c.approve ? <button type="button" className={primary} onClick={() => act("approve")}>Approve</button> : null}
      {c.reject ? <button type="button" className={secondary} onClick={() => act("reject")}>Reject</button> : null}
      {c.retire ? <button type="button" className={secondary} onClick={() => act("retire")}>Retire</button> : null}
      {msg ? <span className="text-[#9B2335]">{msg}</span> : null}
    </div>
  );
}

function SetForm({ onDone }) {
  const [sec, setSec] = useState(null);
  const [rows, setRows] = useState([{ sec: null, reason: "", evidence: "" }]);
  const [msg, setMsg] = useState(null);
  async function submit() {
    setMsg(null);
    try {
      await post("tax/substitutions/sets", {
        global_security_id: sec?.id,
        candidates: rows.filter((r) => r.sec).map((r) => ({ replacement_security_id: r.sec.id, reason: r.reason, evidence: r.evidence })),
      });
      setMsg("Proposed — a second person approves it.");
      if (onDone) await onDone();
    } catch (e) { setMsg(e.message); }
  }
  return (
    <div className="mt-3 space-y-2 border-t border-[#ece8dd] pt-3">
      <p className="font-medium">Propose a substitution set</p>
      <SecurityPicker value={sec} onChange={setSec} placeholder="The security to replace" />
      {rows.map((r, i) => (
        <div key={i} className="space-y-1 rounded border border-[#ece8dd] p-2">
          <p className="text-xs text-text-muted">Candidate {i + 1}</p>
          <SecurityPicker value={r.sec} onChange={(s) => setRows(rows.map((x, j) => (j === i ? { ...x, sec: s } : x)))} placeholder="Replacement" />
          <input className={input} placeholder="Reason (e.g. same industry)" value={r.reason} onChange={(e) => setRows(rows.map((x, j) => (j === i ? { ...x, reason: e.target.value } : x)))} />
          <input className={input} placeholder="Evidence (e.g. different index provider)" value={r.evidence} onChange={(e) => setRows(rows.map((x, j) => (j === i ? { ...x, evidence: e.target.value } : x)))} />
        </div>
      ))}
      <div className="flex gap-2">
        <button type="button" className={secondary} onClick={() => setRows([...rows, { sec: null, reason: "", evidence: "" }])}>Add a candidate</button>
        <button type="button" className={primary} onClick={submit}>Send for approval</button>
      </div>
      {msg ? <p className="text-sm text-text-muted">{msg}</p> : null}
    </div>
  );
}

function DenialForm({ onDone }) {
  const [a, setA] = useState(null);
  const [b, setB] = useState(null);
  const [reason, setReason] = useState("");
  const [evidence, setEvidence] = useState("");
  const [msg, setMsg] = useState(null);
  async function submit() {
    setMsg(null);
    try {
      await post("tax/substitutions/denials", { security_a_id: a?.id, security_b_id: b?.id, reason, evidence });
      setMsg("Proposed — a second person approves it.");
      if (onDone) await onDone();
    } catch (e) { setMsg(e.message); }
  }
  return (
    <div className="mt-3 space-y-2 border-t border-[#ece8dd] pt-3">
      <p className="font-medium">Deny a pair (substantially identical)</p>
      <SecurityPicker value={a} onChange={setA} placeholder="First security" />
      <SecurityPicker value={b} onChange={setB} placeholder="Second security" />
      <input className={input} placeholder="Reason" value={reason} onChange={(e) => setReason(e.target.value)} />
      <input className={input} placeholder="Evidence" value={evidence} onChange={(e) => setEvidence(e.target.value)} />
      <button type="button" className={primary} onClick={submit}>Send for approval</button>
      {msg ? <p className="text-sm text-text-muted">{msg}</p> : null}
    </div>
  );
}

export default function Substitutions({ envelope, onReload }) {
  const [msg, setMsg] = useState(null);
  const v = vocab(envelope);
  const c = pageControls(envelope);
  const sets = Array.isArray(envelope?.sets) ? envelope.sets : [];
  const denials = Array.isArray(envelope?.denials) ? envelope.denials : [];
  async function seed() {
    setMsg(null);
    try { const d = await post("tax/substitutions/denials/seed"); setMsg(`${(d.seeded || []).length} rule denial(s) added.`); if (onReload) await onReload(); }
    catch (e) { setMsg(e.message); }
  }
  return (
    <div className="space-y-5">
      <div className={`${card} p-6 text-sm`}>
        <h2 className="text-lg font-semibold text-navy">Substitution sets</h2>
        <p className="mt-1 text-text-muted">Only an approved set is used. A model's alternates are a starting point, never a pair by themselves.</p>
        {sets.length ? sets.map((s) => (
          <div key={s.id} className="mt-3 border-t border-[#ece8dd] pt-2">
            <p><span className="font-medium text-navy">{s.security}</span> · v{s.version_no} · {labelOf(v.set_statuses, s.status)}{s.current ? "" : " (an earlier version)"}</p>
            <ol className="ml-5 list-decimal">{(s.candidates || []).map((x) => (
              <li key={x.replacement_security_id}>{x.label} — {x.reason}; evidence: {x.evidence} ({labelOf(v.candidate_sources, x.source)})</li>
            ))}</ol>
            {s.decision_note ? <p className="text-text-muted">Note: {s.decision_note}</p> : null}
            <Decide envelope={envelope} row={s} base="tax/substitutions/sets" onDone={onReload} />
          </div>
        )) : <p className="mt-2 text-text-muted">No set yet.</p>}
        {c.proposeSet ? <SetForm onDone={onReload} /> : null}
      </div>
      <div className={`${card} p-6 text-sm`}>
        <h2 className="text-lg font-semibold text-navy">Denials — never pair</h2>
        <p className="mt-1 text-text-muted">A denial overrides a set. A security and itself are always identical.</p>
        {c.seedDenials ? <button type="button" className={`${secondary} mt-2`} onClick={seed}>Seed the rule denials</button> : null}
        {denials.length ? (
          <ul className="mt-2 space-y-2">{denials.map((d) => (
            <li key={d.id} className="border-t border-[#ece8dd] pt-2">{d.security_a} ↔ {d.security_b} · {labelOf(v.denial_rules, d.rule)} · {labelOf(v.denial_statuses, d.status)} — {d.reason}
              <Decide envelope={envelope} row={d} base="tax/substitutions/denials" onDone={onReload} /></li>
          ))}</ul>
        ) : <p className="mt-2 text-text-muted">No denial yet.</p>}
        {c.proposeDenial ? <DenialForm onDone={onReload} /> : null}
        {msg ? <p className="mt-2 text-text-muted">{msg}</p> : null}
      </div>
    </div>
  );
}

export { Decide };
