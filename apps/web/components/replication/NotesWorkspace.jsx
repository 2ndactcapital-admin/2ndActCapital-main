"use client";

/**
 * NotesWorkspace — Replication → Notes (REP11). Every structured note whose
 * payoff terms were extracted from its 424B2: the replicability verdict with
 * its plain reasons, the at-expiry payoff of the note against a replica built
 * from listed / FLEX options (plus a Treasury where the note promises
 * principal), the differences that matter, and — only when every quote exists —
 * the replica's cost against the note's price (an ESTIMATE). Nothing trades:
 * every leg is a manual instruction. Controls from lib/rep11.mjs (fail closed).
 */

import { useState } from "react";
import { estimateText, labelOf, listControls, noteControls, overlay, pct, per100, replicaControls, vocab } from "@/lib/rep11.mjs";

const card = "rounded-[6px] border border-[#ece8dd] bg-white";
const input = "rounded border border-[#E2E8F0] px-3 py-2";
const primary = "rounded bg-navy px-3 py-1 text-white disabled:opacity-50";
const secondary = "rounded border border-[#E2E8F0] px-3 py-1 text-navy";

async function call(path, body) {
  const res = await fetch(`/api/replication/${path}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const d = data.detail;
    const reasons = d && typeof d === "object" && Array.isArray(d.reasons) ? ` ${d.reasons.join(" ")}` : "";
    throw new Error((data.error || "Request failed") + reasons);
  }
  return data;
}

export function VerdictBadge({ envelope, verdict }) {
  if (!verdict) return <span className="text-text-muted">no verdict yet</span>;
  const tone = verdict.verdict === "not_replicable" ? "text-[#9B2335]" : "text-[#2D6A4F]";
  return (
    <span>
      <span className={`font-semibold ${tone}`}>{labelOf(vocab(envelope).verdicts, verdict.verdict)}</span>
      {verdict.is_preliminary ? <span className="ml-2 text-xs text-text-muted">({verdict.preliminary_text})</span> : null}
      {verdict.needs_review ? <span className="ml-2 text-xs text-[#9B2335]">terms need review</span> : null}
    </span>
  );
}

export function NotesListView({ envelope, onFilter, onRun }) {
  const v = vocab(envelope);
  const c = listControls(envelope);
  const rows = Array.isArray(envelope?.rows) ? envelope.rows : [];
  const [f, setF] = useState({ verdict: "", archetype: "", issuer: "", underlying: "" });
  const counts = envelope?.counts || {};
  return (
    <div className="space-y-4 text-sm">
      <div className={`${card} flex flex-wrap items-end gap-2 p-4`}>
        <select className={input} value={f.verdict} onChange={(e) => setF({ ...f, verdict: e.target.value })}>
          <option value="">Every verdict</option>
          {(v.verdicts || []).map((x) => <option key={x.key} value={x.key}>{x.label} ({counts[x.key] ?? 0})</option>)}
        </select>
        <select className={input} value={f.archetype} onChange={(e) => setF({ ...f, archetype: e.target.value })}>
          <option value="">Every archetype</option>
          {(v.archetypes || []).map((x) => <option key={x.key} value={x.key}>{x.label}</option>)}
        </select>
        <input className={input} placeholder="Issuer" value={f.issuer} onChange={(e) => setF({ ...f, issuer: e.target.value })} />
        <input className={input} placeholder="Underlying" value={f.underlying} onChange={(e) => setF({ ...f, underlying: e.target.value })} />
        <button type="button" className={secondary} onClick={() => onFilter?.(f)}>Filter</button>
        {c.runVerdicts ? <button type="button" className={primary} onClick={() => onRun?.()}>Recompute verdicts</button> : null}
      </div>
      {rows.length ? (
        <table className={`${card} w-full`}>
          <thead className="text-left text-text-muted"><tr><th className="p-2">Note</th><th>Issuer</th><th>Shape</th><th>Underlying</th><th>Verdict</th></tr></thead>
          <tbody>{rows.map((r) => (
            <tr key={r.global_security_id} className="border-t border-[#ece8dd] align-top">
              <td className="p-2"><a className="text-navy underline" href={`/portfolio/replication/notes/${r.global_security_id}`}>{r.name}</a>
                {r.cusip ? <span className="ml-1 text-xs text-text-muted">{r.cusip}</span> : null}</td>
              <td>{r.credit_entity || r.issuer}</td>
              <td>{labelOf(v.archetypes, r.product_archetype)} · {labelOf(v.basket_types, r.basket_type)}</td>
              <td>{r.underlyings}</td>
              <td><VerdictBadge envelope={envelope} verdict={r.verdict} /></td>
            </tr>
          ))}</tbody>
        </table>
      ) : <div className={`${card} p-6 text-center text-text-muted`}>No note matches.</div>}
    </div>
  );
}

export function PayoffOverlay({ points }) {
  const o = overlay(points);
  if (!o) return <p className="text-text-muted">Payoff not drawn.</p>;
  return (
    <div>
      <svg width={o.width} height={o.height} role="img" aria-label="Payoff at expiry: note and replica">
        <line x1="0" x2={o.width} y1={o.zeroY} y2={o.zeroY} stroke="#E2E8F0" />
        <line y1="0" y2={o.height} x1={o.zeroX} x2={o.zeroX} stroke="#E2E8F0" />
        <path d={o.note} fill="none" stroke="#1B2B4B" strokeWidth="2" />
        <path d={o.replica} fill="none" stroke="#C5A880" strokeWidth="2" strokeDasharray="5 4" />
      </svg>
      <p className="text-xs text-text-muted">Navy: the note's stated payoff · gold dashed: the replica · {o.diffs ? `${o.diffs} of ${o.count} grid points differ` : `identical at all ${o.count} grid points`}</p>
    </div>
  );
}

function TermsTable({ envelope, note }) {
  const v = vocab(envelope);
  const rows = [
    ["Shape", `${labelOf(v.archetypes, note.product_archetype)} · ${labelOf(v.basket_types, note.basket_type)}`],
    ["Protection", `${labelOf(v.protection_types, note.protection_type)} ${note.protection_pct ? pct(note.protection_pct) : ""}`],
    ["Cap", note.cap_pct ? pct(note.cap_pct) : "—"], ["Participation", note.participation_rate ? `${note.participation_rate}×` : "—"],
    ["Coupon", note.coupon_rate ? pct(note.coupon_rate) : "—"], ["Outcome period", `${note.initial_valuation_date || "—"} to ${note.final_valuation_date || "—"}`],
    ["Terms", `${note.terms_status} · extraction ${note.extraction_confidence || "—"}`], ["Issuer (credit)", note.credit_entity || "—"],
  ];
  return (
    <table className="w-full"><tbody>{rows.map(([k, val]) => (
      <tr key={k} className="border-t border-[#ece8dd]"><td className="py-1 text-text-muted">{k}</td><td>{val}</td></tr>))}</tbody></table>
  );
}

export function NoteDetailView({ envelope, onBuild }) {
  const v = vocab(envelope);
  const c = noteControls(envelope);
  const note = envelope?.note;
  const [f, setF] = useState({ broad_based_index: true, start_level_quote_id: "", treasury_global_security_id: "" });
  if (!note) return null;
  const verdict = envelope.verdict;
  const payoff = envelope.payoff;
  const dist = envelope.distribution || {};
  return (
    <div className="space-y-4 text-sm">
      <div className={`${card} p-6`}>
        <h2 className="text-lg font-semibold text-navy">{note.name}</h2>
        <p className="text-text-muted">{note.cusip || ""} · {note.filing?.form_type} filed {note.filing?.filing_date} {note.filing?.url ? <a className="text-navy underline" href={note.filing.url}>the filing</a> : null}</p>
        <div className="mt-3"><TermsTable envelope={envelope} note={note} /></div>
        <p className="mt-2">Distribution and fees: {dist.known ? Object.entries(dist.fields || {}).map(([k, x]) => `${k}: ${JSON.stringify(x.value)} (${x.label})`).join("; ") : dist.text}</p>
      </div>
      <div className={`${card} p-6`}>
        <h2 className="text-lg font-semibold text-navy">Verdict</h2>
        <p className="mt-1"><VerdictBadge envelope={envelope} verdict={verdict} />{verdict?.mapping_label ? ` — ${verdict.mapping_label}` : ""}</p>
        <ul className="mt-2 list-disc pl-5">{(verdict?.reasons || []).map((r) => <li key={r.code}>{r.text}</li>)}</ul>
        {(verdict?.review_reasons || []).length ? <ul className="mt-2 list-disc pl-5 text-[#9B2335]">{verdict.review_reasons.map((r) => <li key={r.code}>{r.text}</li>)}</ul> : null}
        <p className="mt-2 text-text-muted">Underlying: {envelope.underlying?.text}</p>
      </div>
      {payoff ? (
        <div className={`${card} p-6`}>
          <h2 className="text-lg font-semibold text-navy">Payoff at expiry — the note and a replica</h2>
          {payoff.drawn ? <><PayoffOverlay points={payoff.points} /><p className="text-xs text-text-muted">{payoff.note}</p>
            {payoff.approximation ? <p className="mt-1">{payoff.approximation.text}</p> : null}
            <ul className="mt-2">{(payoff.legs || []).map((l, i) => <li key={i}>{l.purpose}</li>)}</ul></> : <p className="text-text-muted">{payoff.text}</p>}
        </div>
      ) : null}
      <div className={`${card} p-6`}>
        <h2 className="text-lg font-semibold text-navy">Replicas</h2>
        {(envelope.replicas || []).length ? <ul className="mt-1">{envelope.replicas.map((r) => (
          <li key={r.id}><a className="text-navy underline" href={`/portfolio/replication/notes/replicas/${r.id}`}>{labelOf(v.replica_statuses, r.status)}</a> — {r.mapping_label}, built {r.created_at}</li>))}</ul>
          : <p className="text-text-muted">No replica of this note in the firm.</p>}
        {!envelope.buildable?.ok ? <ul className="mt-2 text-text-muted">{(envelope.buildable?.why || []).map((w, i) => <li key={i}>{w}</li>)}</ul> : null}
        {c.build ? (
          <div className="mt-3 flex flex-wrap items-end gap-2">
            <label className="flex items-center gap-1"><input type="checkbox" checked={f.broad_based_index} onChange={(e) => setF({ ...f, broad_based_index: e.target.checked })} /> Broad-based index</label>
            <input className={input} placeholder="Start-level quote id (optional)" value={f.start_level_quote_id} onChange={(e) => setF({ ...f, start_level_quote_id: e.target.value })} />
            <input className={input} placeholder="Treasury security id (optional)" value={f.treasury_global_security_id} onChange={(e) => setF({ ...f, treasury_global_security_id: e.target.value })} />
            <button type="button" className={primary} onClick={() => onBuild?.(f)}>Build a replica</button>
          </div>
        ) : null}
      </div>
      <div className={`${card} p-6`}>
        <h2 className="text-lg font-semibold text-navy">What differs</h2>
        <ul className="mt-1 space-y-1">{(envelope.differences || []).map((d) => <li key={d.key}><span className="font-semibold">{d.label}.</span> {d.text}</li>)}</ul>
        <ul className="mt-3 text-xs text-text-muted">{(v.counsel_items || []).map((x, i) => <li key={i}>{x}</li>)}</ul>
      </div>
    </div>
  );
}

export function ReplicaView({ envelope, onAct }) {
  const v = vocab(envelope);
  const c = replicaControls(envelope);
  const r = envelope?.replica;
  const [note, setNote] = useState("");
  if (!r) return null;
  const cmp = envelope.comparison;
  return (
    <div className="space-y-4 text-sm">
      <div className={`${card} p-6`}>
        <h2 className="text-lg font-semibold text-navy">Replica of {envelope.note?.name}</h2>
        <p className="text-text-muted">{labelOf(v.replica_statuses, r.status)} · {r.mapping_label} · outcome {r.outcome_start} to {r.outcome_end}</p>
        <p>Start level: {r.start_level ?? r.start_level_text}</p>
        {r.decision_note ? <p className="text-text-muted">Decision note: {r.decision_note}</p> : null}
        <p className="mt-1 text-text-muted">{envelope.manual_only}</p>
        {(envelope.needs || []).length ? <ul className="mt-1 text-[#9B2335]">{envelope.needs.map((n, i) => <li key={i}>{n}</li>)}</ul> : null}
      </div>
      <div className={`${card} p-6`}>
        <h2 className="text-lg font-semibold text-navy">Legs — each a manual instruction</h2>
        <ol className="mt-1 list-decimal space-y-1 pl-5">{(envelope.legs || []).map((l) => <li key={l.id}>{l.instruction}</li>)}</ol>
      </div>
      <div className={`${card} p-6`}>
        <h2 className="text-lg font-semibold text-navy">Computed against stated</h2>
        <table className="mt-1 w-full"><thead className="text-left text-text-muted"><tr><th>Term</th><th>Note states</th><th>Replica computes</th><th>Difference</th></tr></thead>
          <tbody>{(envelope.computed?.rows || []).map((x) => <tr key={x.term} className="border-t border-[#ece8dd]"><td className="py-1">{x.term}</td><td>{x.stated ?? "—"}</td><td>{x.computed ?? "—"}</td><td>{x.equal ? "none" : (x.difference ?? "—")}</td></tr>)}</tbody></table>
        {envelope.approximation ? <p className="mt-2">{envelope.approximation.text}</p> : null}
        <div className="mt-3"><PayoffOverlay points={envelope.payoff?.points} /></div>
      </div>
      <div className={`${card} p-6`}>
        <h2 className="text-lg font-semibold text-navy">Cost against the note's price</h2>
        <p className="mt-1">{estimateText(cmp)}</p>
        {cmp && !cmp.withheld ? <ul className="mt-1 text-xs text-text-muted">{(cmp.inputs || []).map((i, k) => <li key={k}>{i.label}: {i.value}{i.as_of ? ` as of ${i.as_of}` : ""}{i.source ? ` (${i.source})` : ""}{i.derived ? ` — ${i.derived}` : ""}</li>)}</ul> : null}
        {cmp?.method ? <p className="mt-1 text-xs text-text-muted">{cmp.method}</p> : null}
        <p className="mt-1 text-xs text-text-muted">{v.ssvi?.text}</p>
        {c.price ? <button type="button" className={`${secondary} mt-2`} onClick={() => onAct?.("price", {})}>Price now</button> : null}
      </div>
      <div className="flex flex-wrap gap-2">
        {c.submit ? <button type="button" className={primary} onClick={() => onAct?.("submit", {})}>Submit for approval</button> : null}
        {c.approve ? <button type="button" className={primary} onClick={() => onAct?.("approve", { note: note || null })}>Approve</button> : null}
        {c.reject || c.archive ? <input className={input} placeholder="Note / reason" value={note} onChange={(e) => setNote(e.target.value)} /> : null}
        {c.reject ? <button type="button" className={secondary} onClick={() => onAct?.("reject", { note })}>Reject</button> : null}
        {c.archive ? <button type="button" className={secondary} onClick={() => onAct?.("archive", { reason: note })}>Archive</button> : null}
      </div>
    </div>
  );
}

export function NotesList({ initialEnvelope }) {
  const [env, setEnv] = useState(initialEnvelope);
  const [msg, setMsg] = useState(null);
  async function load(f) {
    const q = new URLSearchParams(Object.entries(f || {}).filter(([, x]) => x)).toString();
    const res = await fetch(`/api/replication/notes${q ? `?${q}` : ""}`);
    if (res.ok) setEnv(await res.json());
  }
  async function run() {
    setMsg(null);
    try { const o = await call("notes/verdicts/run", {}); setMsg(`Verdicts: ${o.result?.computed ?? 0} computed, ${o.result?.unchanged ?? 0} unchanged.`); await load({}); } catch (e) { setMsg(e.message); }
  }
  return <div className="space-y-3">{msg ? <p className="text-sm text-navy">{msg}</p> : null}<NotesListView envelope={env} onFilter={load} onRun={run} /></div>;
}

export function NoteDetail({ initialEnvelope }) {
  const [msg, setMsg] = useState(null);
  async function build(f) {
    setMsg(null);
    try {
      const body = { broad_based_index: f.broad_based_index };
      if (f.start_level_quote_id) body.start_level_quote_id = f.start_level_quote_id;
      if (f.treasury_global_security_id) body.treasury_global_security_id = f.treasury_global_security_id;
      const o = await call(`notes/${initialEnvelope.note.global_security_id}/replicas`, body);
      window.location.href = `/portfolio/replication/notes/replicas/${o.replica.id}`;
    } catch (e) { setMsg(e.message); }
  }
  return <div className="space-y-3">{msg ? <p className="text-sm text-[#9B2335]">{msg}</p> : null}<NoteDetailView envelope={initialEnvelope} onBuild={build} /></div>;
}

export function ReplicaDetail({ initialEnvelope }) {
  const [env, setEnv] = useState(initialEnvelope);
  const [msg, setMsg] = useState(null);
  async function act(action, body) {
    setMsg(null);
    try { setEnv(await call(`notes/replicas/${env.replica.id}/${action}`, body)); } catch (e) { setMsg(e.message); }
  }
  return <div className="space-y-3">{msg ? <p className="text-sm text-[#9B2335]">{msg}</p> : null}<ReplicaView envelope={env} onAct={act} /></div>;
}

export default NotesList;
