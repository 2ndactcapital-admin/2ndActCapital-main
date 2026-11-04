"use client";

/**
 * NoteChecksWorkspace — REP12. Replication → Notes → New issues: every new or
 * restated note checked automatically against what it is made of — the
 * verdict, a DRAFT replica when the note is eligible (never approved here),
 * and the embedded-fee ESTIMATE where the note's price and every leg are
 * quoted ("not priced — …" naming what is missing otherwise). And the Super
 * Admin's underlying queue: one-click / multi-confirm for edges with a single
 * registry proposal (each its own confirmation), the manual match for the rest.
 * Controls from lib/rep12.mjs (fail closed); labels from the envelope.
 */

import { useState } from "react";
import { confirmable, estimateText, feedControls, feedQuery, isPriced, labelOf, queueControls, rowControls, splitList, vocab } from "@/lib/rep12.mjs";

const card = "rounded-[6px] border border-[#ece8dd] bg-white";
const input = "rounded border border-[#E2E8F0] px-3 py-2";
const primary = "rounded bg-navy px-3 py-1 text-white disabled:opacity-50";
const secondary = "rounded border border-[#E2E8F0] px-3 py-1 text-navy disabled:opacity-50";

async function send(path, method = "GET", body) {
  const res = await fetch(`/api/replication/${path}`, {
    method, headers: body ? { "Content-Type": "application/json" } : undefined, body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || "Request failed");
  return data;
}

function Rules({ envelope }) {
  const v = vocab(envelope);
  const rules = Array.isArray(v.rules) ? v.rules : [];
  return (
    <ul className="list-disc pl-5 text-xs text-text-muted">
      {rules.map((r) => <li key={r}>{r}</li>)}
      {v.new_issue_rule ? <li>{v.new_issue_rule}</li> : null}
    </ul>
  );
}

function SettingsForm({ envelope, onSave }) {
  const s = envelope?.settings || {};
  const v = vocab(envelope);
  const [f, setF] = useState({
    enabled: s.enabled !== false, fee_alert_threshold_pct: s.fee_alert_threshold_pct || "",
    watch_archetypes: Array.isArray(s.watch_archetypes) ? s.watch_archetypes : [],
    watch_issuers: (s.watch_issuers || []).join(", "), watch_underlyings: (s.watch_underlyings || []).join(", "),
  });
  const toggle = (k) => setF({ ...f, watch_archetypes: f.watch_archetypes.includes(k) ? f.watch_archetypes.filter((x) => x !== k) : [...f.watch_archetypes, k] });
  return (
    <div className={`${card} space-y-2 p-4`}>
      <h3 className="font-semibold text-navy">Alerts</h3>
      <label className="flex items-center gap-2"><input type="checkbox" checked={f.enabled} onChange={(e) => setF({ ...f, enabled: e.target.checked })} /> Check new issues for this firm</label>
      <label className="block">Alert when the embedded-fee estimate is above (per 100 of principal)
        <input className={`${input} ml-2 w-28`} value={f.fee_alert_threshold_pct} onChange={(e) => setF({ ...f, fee_alert_threshold_pct: e.target.value })} placeholder="e.g. 2.5" /></label>
      <div className="flex flex-wrap gap-3">{(v.archetypes || []).map((a) => (
        <label key={a.key} className="flex items-center gap-1"><input type="checkbox" checked={f.watch_archetypes.includes(a.key)} onChange={() => toggle(a.key)} />{a.label}</label>))}</div>
      <input className={`${input} w-full`} placeholder="Watched issuers (comma-separated)" value={f.watch_issuers} onChange={(e) => setF({ ...f, watch_issuers: e.target.value })} />
      <input className={`${input} w-full`} placeholder="Watched underlyings (comma-separated)" value={f.watch_underlyings} onChange={(e) => setF({ ...f, watch_underlyings: e.target.value })} />
      <button type="button" className={primary} onClick={() => onSave?.({
        enabled: f.enabled, fee_alert_threshold_pct: f.fee_alert_threshold_pct.trim() || null, watch_archetypes: f.watch_archetypes,
        watch_issuers: splitList(f.watch_issuers), watch_underlyings: splitList(f.watch_underlyings),
      })}>Save alerts</button>
    </div>
  );
}

export function NewIssuesView({ envelope, onFilter, onReview, onRun, onSettings }) {
  const v = vocab(envelope);
  const c = feedControls(envelope);
  const rows = Array.isArray(envelope?.rows) ? envelope.rows : [];
  const [f, setF] = useState({ verdict: "", archetype: "", issuer: "", underlying: "", date_from: "", date_to: "", status: "", sort: envelope?.sort || "newest" });
  const [notes, setNotes] = useState({});
  return (
    <div className="space-y-4 text-sm">
      <Rules envelope={envelope} />
      <div className={`${card} flex flex-wrap items-end gap-2 p-4`}>
        <select className={input} value={f.verdict} onChange={(e) => setF({ ...f, verdict: e.target.value })}>
          <option value="">Every verdict</option>
          {(v.verdicts || []).map((x) => <option key={x.key} value={x.key}>{x.label}</option>)}
        </select>
        <select className={input} value={f.archetype} onChange={(e) => setF({ ...f, archetype: e.target.value })}>
          <option value="">Every archetype</option>
          {(v.archetypes || []).map((x) => <option key={x.key} value={x.key}>{x.label}</option>)}
        </select>
        <input className={input} placeholder="Issuer" value={f.issuer} onChange={(e) => setF({ ...f, issuer: e.target.value })} />
        <input className={input} placeholder="Underlying" value={f.underlying} onChange={(e) => setF({ ...f, underlying: e.target.value })} />
        <input className={input} type="date" aria-label="Issued from" value={f.date_from} onChange={(e) => setF({ ...f, date_from: e.target.value })} />
        <input className={input} type="date" aria-label="Issued to" value={f.date_to} onChange={(e) => setF({ ...f, date_to: e.target.value })} />
        <select className={input} value={f.status} onChange={(e) => setF({ ...f, status: e.target.value })}>
          <option value="">Every status</option>
          {(v.statuses || []).map((x) => <option key={x.key} value={x.key}>{x.label}</option>)}
        </select>
        <select className={input} value={f.sort} onChange={(e) => setF({ ...f, sort: e.target.value })}>
          {(v.sorts || []).map((x) => <option key={x.key} value={x.key}>{x.label}</option>)}
        </select>
        <button type="button" className={secondary} onClick={() => onFilter?.(f)}>Filter</button>
        {c.run ? <button type="button" className={primary} onClick={() => onRun?.()}>Check now</button> : null}
      </div>
      {c.settings ? <SettingsForm envelope={envelope} onSave={onSettings} /> : null}
      {rows.length ? (
        <table className={`${card} w-full`}>
          <thead className="text-left text-text-muted"><tr><th className="p-2">Issued</th><th>Note</th><th>Issuer</th><th>Underlying</th><th>Verdict</th><th>Replica</th><th>Embedded fee</th><th>Status</th><th /></tr></thead>
          <tbody>{rows.map((r) => {
            const rc = rowControls(envelope, r);
            return (
              <tr key={r.id} className="border-t border-[#ece8dd] align-top">
                <td className="p-2">{r.issued_on}<div className="text-xs text-text-muted">{r.issued_basis_label}</div></td>
                <td><a className="text-navy underline" href={r.note?.link}>{r.note?.name}</a>
                  <div className="text-xs text-text-muted">{r.note?.archetype_label}{r.note?.cusip ? ` · ${r.note.cusip}` : ""}</div></td>
                <td>{r.note?.issuer || "—"}</td>
                <td>{r.underlyings}<div className="text-xs text-text-muted">{labelOf(v.underlying_states, r.underlying_state)}</div></td>
                <td>{labelOf(v.verdicts, r.verdict)}</td>
                <td>{r.replica_link ? <a className="text-navy underline" href={r.replica_link}>{labelOf(v.replica_outcomes, r.replica_outcome)}</a> : labelOf(v.replica_outcomes, r.replica_outcome)}
                  {(r.not_built_reasons || []).length ? <ul className="list-disc pl-4 text-xs text-text-muted">{r.not_built_reasons.map((x) => <li key={x}>{x}</li>)}</ul> : null}</td>
                <td className={isPriced(r) ? "font-semibold text-navy" : "text-text-muted"}>{estimateText(r)}</td>
                <td>{labelOf(v.statuses, r.status)}
                  {(r.alert_reasons || []).length ? <div className="text-xs text-[#9B2335]">{r.alert_reasons.map((a) => a.text).join(" · ")}</div> : null}</td>
                <td className="space-y-1">
                  {rc.review || rc.dismiss ? <input className={`${input} w-40`} placeholder="Note" value={notes[r.id] || ""} onChange={(e) => setNotes({ ...notes, [r.id]: e.target.value })} /> : null}
                  {rc.review ? <button type="button" className={secondary} onClick={() => onReview?.(r.id, "reviewed", notes[r.id])}>{labelOf(v.review_outcomes, "reviewed")}</button> : null}
                  {rc.dismiss ? <button type="button" className={secondary} onClick={() => onReview?.(r.id, "dismissed", notes[r.id])}>{labelOf(v.review_outcomes, "dismissed")}</button> : null}
                </td>
              </tr>
            );
          })}</tbody>
        </table>
      ) : <div className={`${card} p-6 text-center text-text-muted`}>No new issue has been checked yet.</div>}
    </div>
  );
}

export function NewIssues({ initialEnvelope }) {
  const [env, setEnv] = useState(initialEnvelope);
  const [q, setQ] = useState("");
  const [msg, setMsg] = useState(null);
  const load = async (query = q) => {
    setEnv(await send(`notes/checks${query ? `?${query}` : ""}`));
  };
  const act = async (fn) => {
    setMsg(null);
    try { await fn(); await load(); } catch (e) { setMsg(e.message); }
  };
  return (
    <div className="space-y-3">
      {msg ? <div className={`${card} p-3 text-sm text-[#9B2335]`}>{msg}</div> : null}
      <NewIssuesView envelope={env}
        onFilter={(f) => act(async () => { const query = feedQuery(f); setQ(query); await load(query); })}
        onRun={() => act(() => send("notes/checks/run", "POST", {}))}
        onSettings={(body) => act(() => send("notes/checks/settings", "POST", body))}
        onReview={(id, outcome, note) => act(() => send(`notes/checks/${id}/review`, "POST", { outcome, note: note || null }))} />
    </div>
  );
}

export function UnderlyingQueueView({ envelope, onConfirm, onReject }) {
  const v = vocab(envelope);
  const counts = envelope?.counts || {};
  const groups = envelope?.groups || {};
  const one = Array.isArray(groups.one_proposal) ? groups.one_proposal : [];
  const manual = Array.isArray(groups.manual) ? groups.manual : [];
  const [sel, setSel] = useState([]);
  const pick = (id) => setSel(sel.includes(id) ? sel.filter((x) => x !== id) : [...sel, id]);
  const ready = confirmable(envelope, sel);
  return (
    <div className="space-y-4 text-sm">
      <div className={`${card} flex flex-wrap gap-6 p-4`}>
        {["resolved", "ambiguous", "unresolved"].map((k) => (
          <div key={k}><div className="text-2xl font-semibold text-navy">{counts[k] ?? 0}</div><div className="text-text-muted">{labelOf(v.underlying_states, k)}</div></div>))}
      </div>
      <p className="text-xs text-text-muted">{(v.rules || [])[0]}</p>
      <div className={`${card} p-4`}>
        <div className="flex items-center justify-between">
          <h3 className="font-semibold text-navy">{labelOf(v.queue_groups, "one_proposal")} ({one.length})</h3>
          {ready.length ? <button type="button" className={primary} onClick={() => onConfirm?.(ready)}>Confirm {ready.length} selected</button> : null}
        </div>
        <table className="mt-2 w-full"><tbody>{one.map((it) => {
          const qc = queueControls(envelope, it);
          return (
            <tr key={it.id} className="border-t border-[#ece8dd] align-top">
              <td className="p-2">{qc.confirm ? <input type="checkbox" aria-label={`select ${it.raw_underlying_text}`} checked={sel.includes(it.id)} onChange={() => pick(it.id)} /> : null}</td>
              <td>{it.raw_underlying_text}<div className="text-xs text-text-muted">proposed: {it.proposal?.security_name || "—"}</div></td>
              <td><a className="text-navy underline" href={it.note?.link}>{it.note?.name}</a><div className="text-xs text-text-muted">{it.note?.filer_name}</div></td>
              <td className="space-x-1">
                {qc.confirm ? <button type="button" className={secondary} onClick={() => onConfirm?.([it.id])}>Confirm</button> : null}
                {qc.reject ? <button type="button" className={secondary} onClick={() => onReject?.(it.id)}>Reject</button> : null}
              </td>
            </tr>
          );
        })}</tbody></table>
      </div>
      <div className={`${card} p-4`}>
        <h3 className="font-semibold text-navy">{labelOf(v.queue_groups, "manual")} ({manual.length})</h3>
        <p className="text-xs text-text-muted">{envelope?.manual_match?.text}</p>
        <table className="mt-2 w-full"><tbody>{manual.map((it) => (
          <tr key={it.id} className="border-t border-[#ece8dd] align-top">
            <td className="p-2">{it.raw_underlying_text}<div className="text-xs text-text-muted">{it.proposal?.kind || "not examined"}</div></td>
            <td><a className="text-navy underline" href={it.note?.link}>{it.note?.name}</a></td>
          </tr>))}</tbody></table>
      </div>
    </div>
  );
}

export function UnderlyingQueue({ initialEnvelope }) {
  const [env, setEnv] = useState(initialEnvelope);
  const [msg, setMsg] = useState(null);
  const act = async (fn) => {
    setMsg(null);
    try {
      const out = await fn();
      setEnv({ ...env, ...(out.queue || {}), permissions: out.permissions, vocabularies: out.vocabularies });
      if (Array.isArray(out.results)) {
        const bad = out.results.filter((r) => !r.ok);
        setMsg(`${out.confirmed} confirmed${bad.length ? `; refused: ${bad.map((b) => b.error).join("; ")}` : ""}`);
      }
    } catch (e) { setMsg(e.message); }
  };
  return (
    <div className="space-y-3">
      {msg ? <div className={`${card} p-3 text-sm text-navy`}>{msg}</div> : null}
      <UnderlyingQueueView envelope={env}
        onConfirm={(ids) => act(() => send("notes/underlyings/confirm", "POST", { relationship_ids: ids }))}
        onReject={(id) => act(() => send(`notes/underlyings/${id}/reject`, "POST", {}))} />
    </div>
  );
}
