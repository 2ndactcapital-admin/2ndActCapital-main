"use client";

/**
 * TiltsWorkspace — Replication → Tilts (REP10): "the index plus my views" on one
 * account, and the account's Completion. The editor previews on every edit
 * (POST …/preview, nothing written): final weights against the model, active
 * share, the largest single-name and sector deviations, and the projected
 * tracking error — HYPOTHETICAL, or the server's "not estimated — needs prices
 * (x% of weight covered)". Every control comes from lib/rep10.mjs (fail closed).
 */

import { useEffect, useState } from "react";
import { completionControls, deltaFromPoints, labelOf, pct, teText, tiltControls, vocab } from "@/lib/rep10.mjs";

const card = "rounded-[6px] border border-[#ece8dd] bg-white";
const input = "rounded border border-[#E2E8F0] px-3 py-2";
const primary = "rounded bg-navy px-3 py-1 text-white disabled:opacity-50";
const secondary = "rounded border border-navy px-3 py-1 text-navy disabled:opacity-50";

async function call(path, body, method = "POST") {
  const res = await fetch(`/api/replication/${path}`, {
    method, headers: { "Content-Type": "application/json" }, body: method === "GET" ? undefined : JSON.stringify(body || {}),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data?.detail?.message || data.error || `Request failed (${res.status})`);
  return data;
}

export function PreviewView({ preview, envelope }) {
  if (!preview) return null;
  const v = vocab(envelope);
  if (preview.ok === false) {
    return (
      <div className={`${card} p-4 text-sm`}>
        <p className="font-medium text-[#9B2335]">Refused</p>
        <ul className="mt-1 list-disc pl-5">{(preview.refused || []).map((r) => <li key={r}>{r}</li>)}</ul>
      </div>
    );
  }
  const m = preview.metrics || {};
  return (
    <div className={`${card} space-y-3 p-4 text-sm`}>
      <div className="flex flex-wrap gap-6">
        <p>Active share <span className="font-medium text-navy">{pct(m.active_share)}</span></p>
        <p>Largest single name <span className="font-medium text-navy">{m.largest_single_name ? `${m.largest_single_name.label} ${pct(m.largest_single_name.deviation)}` : "—"}</span></p>
        <p>Largest sector <span className="font-medium text-navy">{m.largest_sector ? `${m.largest_sector.sector} ${pct(m.largest_sector.deviation)}` : "—"}</span></p>
        <p>Projected tracking error <span className="font-medium text-navy">{teText(preview.tracking_error)}</span></p>
      </div>
      <p className="text-text-muted">{preview.hypothetical_note || v.hypothetical_note}</p>
      <table className="w-full">
        <thead><tr className="text-left text-text-muted"><th>Security</th><th>Model</th><th>Tilt</th><th>Final</th></tr></thead>
        <tbody>
          {(preview.weights || []).map((w) => (
            <tr key={w.key}><td>{w.label}</td><td>{pct(w.model_weight)}</td><td>{pct(w.tilt)}</td><td>{pct(w.final_weight)}</td></tr>
          ))}
        </tbody>
      </table>
      {(preview.notes || []).map((n) => <p key={n} className="text-text-muted">{n}</p>)}
    </div>
  );
}

export function TiltSetView({ envelope, row, onAct }) {
  const v = vocab(envelope);
  const c = tiltControls(envelope, row);
  const [note, setNote] = useState("");
  return (
    <div className={`${card} p-4 text-sm`}>
      <p className="font-medium text-navy">v{row.version_no} {row.name} — {labelOf(v.tilt_statuses, row.status)}</p>
      <p className="text-text-muted">{row.reason}</p>
      <ul className="mt-2 list-disc pl-5">
        {(row.lines || []).map((l) => <li key={l.id}>{labelOf(v.tilt_kinds, l.kind)}: {l.subject_label || l.subject} {pct(l.delta)} — {l.reason}</li>)}
      </ul>
      {row.decision_note ? <p className="mt-1 text-text-muted">Note: {row.decision_note}</p> : null}
      <div className="mt-2 flex flex-wrap items-center gap-2">
        {c.submit ? <button type="button" className={primary} onClick={() => onAct?.("submit", row)}>Submit for approval</button> : null}
        {c.discard ? <button type="button" className={secondary} onClick={() => onAct?.("archive", row, "discarded")}>Discard draft</button> : null}
        {c.archive ? <button type="button" className={secondary} onClick={() => onAct?.("archive", row, "retired")}>Retire tilt set</button> : null}
        {c.approve || c.reject ? <input className={input} placeholder="Note (required to reject)" value={note} onChange={(e) => setNote(e.target.value)} /> : null}
        {c.approve ? <button type="button" className={primary} onClick={() => onAct?.("approve", row, note)}>Approve</button> : null}
        {c.reject ? <button type="button" className={secondary} onClick={() => onAct?.("reject", row, note)}>Reject</button> : null}
      </div>
    </div>
  );
}

export function TiltsView({ envelope, preview, lines = [], onLines, onAct }) {
  const v = vocab(envelope);
  const c = tiltControls(envelope);
  const rows = Array.isArray(envelope?.rows) ? envelope.rows : [];
  const kinds = Array.isArray(v.tilt_kinds) ? v.tilt_kinds : [];
  return (
    <div className="space-y-4">
      {c.create ? (
        <div className={`${card} space-y-2 p-4 text-sm`}>
          <h2 className="text-lg font-semibold text-navy">New tilt set</h2>
          <p className="text-text-muted">Deltas are percentage points of the account; {v.renormalise_rule}.</p>
          {lines.map((l, i) => (
            <div key={i} className="flex flex-wrap gap-2">
              <select className={input} value={l.kind} onChange={(e) => onLines?.(lines.map((x, j) => (j === i ? { ...x, kind: e.target.value } : x)))}>
                {kinds.map((k) => <option key={k.key} value={k.key}>{k.label}</option>)}
              </select>
              <input className={input} placeholder="Security id, sector or factor" value={l.subject} onChange={(e) => onLines?.(lines.map((x, j) => (j === i ? { ...x, subject: e.target.value } : x)))} />
              <input className={input} placeholder="Points (e.g. 1.5 or -2)" value={l.points} onChange={(e) => onLines?.(lines.map((x, j) => (j === i ? { ...x, points: e.target.value } : x)))} />
              <input className={input} placeholder="Reason" value={l.reason} onChange={(e) => onLines?.(lines.map((x, j) => (j === i ? { ...x, reason: e.target.value } : x)))} />
            </div>
          ))}
          <div className="flex gap-2">
            <button type="button" className={secondary} onClick={() => onLines?.([...lines, { kind: "security", subject: "", points: "", reason: "" }])}>Add line</button>
            <button type="button" className={primary} onClick={() => onAct?.("create")}>Save draft</button>
          </div>
        </div>
      ) : null}
      <PreviewView preview={preview || envelope?.base} envelope={envelope} />
      {rows.length ? rows.map((r) => <TiltSetView key={r.id} envelope={envelope} row={r} onAct={onAct} />)
        : <div className={`${card} p-6 text-center text-sm text-text-muted`}>No tilt set on this account — it follows the model as is.</div>}
    </div>
  );
}

export function CompletionView({ envelope, onAct }) {
  const c = completionControls(envelope);
  const v = vocab(envelope);
  if (!envelope) return null;
  const s = envelope.settings || {};
  return (
    <div className={`${card} space-y-3 p-4 text-sm`}>
      <h2 className="text-lg font-semibold text-navy">Completion</h2>
      <p className="text-text-muted">{envelope.rule}</p>
      <p>Counting holdings elsewhere: <span className="font-medium">{s.count_elsewhere ? "on" : "off"}</span> ({(s.count_authorities || []).map((a) => labelOf(v.authorities, a)).join(", ")})</p>
      {(envelope.reasons || []).map((r) => <p key={r} className="text-[#9B2335]">{r}</p>)}
      {(envelope.needs_confirmation || []).map((f) => (
        <p key={f.account_id} className="text-[#9B2335]">
          {f.account}: data as of {f.as_of || "never"} needs an adviser's confirmation
          {c.confirm ? <button type="button" className={`${secondary} ml-2`} onClick={() => onAct?.("confirm", f)}>Ask to confirm</button> : null}
        </p>
      ))}
      {Array.isArray(envelope.household_check) && envelope.household_check.length ? (
        <table className="w-full">
          <thead><tr className="text-left text-text-muted"><th>Security</th><th>Model</th><th>This account</th><th>Held elsewhere</th><th>Household</th></tr></thead>
          <tbody>{envelope.household_check.map((h) => (
            <tr key={h.key}><td>{h.label}</td><td>{pct(h.model_weight)}</td><td>{pct(h.account_weight)}</td><td>{h.held_elsewhere}</td><td>{pct(h.household_weight)}</td></tr>
          ))}</tbody>
        </table>
      ) : null}
      {(envelope.excess || []).map((e) => <p key={e.key} className="text-text-muted">{e.label}: {e.excess} {e.note}</p>)}
      {c.settings ? (
        <div className="flex gap-2">
          <button type="button" className={primary} onClick={() => onAct?.("completion", { count_elsewhere: !s.count_elsewhere })}>{s.count_elsewhere ? "Turn completion off" : "Turn completion on"}</button>
        </div>
      ) : null}
    </div>
  );
}

export default function TiltsWorkspace({ accountId, initialEnvelope, initialCompletion }) {
  const [env, setEnv] = useState(initialEnvelope);
  const [comp, setComp] = useState(initialCompletion);
  const [lines, setLines] = useState([]);
  const [preview, setPreview] = useState(null);
  const [msg, setMsg] = useState(null);
  const payload = () => lines.filter((l) => l.subject && deltaFromPoints(l.points)).map((l) => ({
    kind: l.kind, subject: l.subject.trim(), delta: deltaFromPoints(l.points), reason: l.reason || undefined }));
  useEffect(() => {
    const body = payload();
    if (!body.length) { setPreview(null); return; }
    const t = setTimeout(() => { call(`tilts/accounts/${accountId}/preview`, { lines: body }).then(setPreview).catch((e) => setMsg(e.message)); }, 300);
    return () => clearTimeout(t);
  }, [JSON.stringify(lines)]); // eslint-disable-line react-hooks/exhaustive-deps
  async function reload() {
    setEnv(await call(`tilts/accounts/${accountId}`, null, "GET"));
    setComp(await call(`completion/accounts/${accountId}`, null, "GET"));
  }
  async function onAct(kind, row, extra) {
    setMsg(null);
    try {
      if (kind === "create") await call(`tilts/accounts/${accountId}`, { name: "Tilts", reason: "Adviser's views", lines: payload() });
      if (kind === "submit") await call(`tilts/sets/${row.id}/submit`, {});
      if (kind === "archive") await call(`tilts/sets/${row.id}/archive`, { reason: extra });
      if (kind === "approve" || kind === "reject") await call(`tilts/sets/${row.id}/${kind}`, { note: extra || undefined });
      if (kind === "completion") await call(`completion/accounts/${accountId}`, { ...row, count_authorities: comp?.settings?.count_authorities || ["read_only", "advise"], reason: "Set from the Completion panel" });
      if (kind === "confirm") await call(`completion/accounts/${accountId}/confirm`, { held_away_account_id: row.account_id });
      setLines([]);
      await reload();
    } catch (e) { setMsg(e.message); }
  }
  return (
    <div className="space-y-5">
      {msg ? <p className="text-sm text-[#9B2335]">{msg}</p> : null}
      <TiltsView envelope={env} preview={preview} lines={lines} onLines={setLines} onAct={onAct} />
      <CompletionView envelope={comp} onAct={onAct} />
    </div>
  );
}
