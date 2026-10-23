"use client";

/**
 * ModelPage — one model (REP3): its live and open versions (positions table,
 * sleeves, look-through, diff, tradability notes), the version history, the
 * sleeves editor for a composite draft, publish to the library, and the model's
 * upload history and log.
 *
 * Every control renders only from `modelControls(envelope)` /
 * `versionControls(versionEnv, permissions)` — the server's own flags AND its
 * `actions` list for this caller and status. Approve / Reject: only with
 * approve_models and never for a maker of the version (`is_maker === false`).
 * Labels are the envelope's vocabularies; weights are the API's exact strings.
 */

import { useState } from "react";
import {
  bandText, labelOf, modelControls, pctInput, pctText, positionLabel, sortPositions, sumPct, versionControls, vocab,
} from "@/lib/models.mjs";

const card = "rounded-[6px] border border-[#ece8dd] bg-white";
const input = "rounded border border-[#E2E8F0] px-3 py-2";
const primary = "rounded bg-navy px-3 py-1 text-white disabled:opacity-50";
const secondary = "rounded border border-navy px-3 py-1 text-navy disabled:opacity-50";

async function call(path, body, method = "POST") {
  const res = await fetch(`/api/models/${path}`, {
    method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = new Error(data.error || `Request failed (${res.status})`);
    err.detail = data.detail;
    throw err;
  }
  return data;
}

export function PositionsTable({ positions, editable = false, onEdit }) {
  const [sort, setSort] = useState({ key: "row", dir: "asc" });
  const rows = sortPositions(positions, sort.key, sort.dir);
  const head = (key, label) => (
    <th className="cursor-pointer px-3 py-2 font-medium" onClick={() => setSort({ key, dir: sort.key === key && sort.dir === "asc" ? "desc" : "asc" })}>
      {label}{sort.key === key ? (sort.dir === "asc" ? " ↑" : " ↓") : ""}
    </th>
  );
  if (!rows.length) return <p className="text-sm text-text-muted">No positions.</p>;
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead className="border-b border-[#ece8dd] text-left text-text-muted">
          <tr>{head("row", "Row")}{head("name", "Position")}{head("weight", "Target")}<th className="px-3 py-2 font-medium">Band</th>
            {head("sleeve", "Sleeve")}{head("resolved", "Resolved")}{editable ? <th className="px-3 py-2" /> : null}</tr>
        </thead>
        <tbody>
          {rows.map((p) => (
            <tr key={p.id || p.row_num} className={`border-b border-[#ece8dd] last:border-0 ${p.resolution_state === "unresolved" ? "bg-[#fdf6ec]" : ""}`}>
              <td className="px-3 py-2">{p.row_num}</td>
              <td className="px-3 py-2">{positionLabel(p)}{p.raw_cusip ? <span className="ml-2 text-xs text-text-muted">{p.raw_cusip}</span> : null}</td>
              <td className="px-3 py-2">{pctText(p.target_weight, 4)}</td>
              <td className="px-3 py-2">{bandText(p)}</td>
              <td className="px-3 py-2">{p.sleeve_label || ""}</td>
              <td className="px-3 py-2">{p.resolution_state === "resolved" ? "Yes" : p.resolution_state === "cash" ? "Cash" : `No — ${p.resolution_note || "queued"}`}</td>
              {editable ? (
                <td className="px-3 py-2 text-right">
                  <button type="button" className="text-navy underline" onClick={() => onEdit?.(p)}>Edit</button>
                </td>
              ) : null}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function DiffView({ diff }) {
  if (!diff || !diff.has_base) return <p className="text-sm text-text-muted">No approved version to compare with yet.</p>;
  return (
    <div className="space-y-2 text-sm">
      <p>{diff.added.length} added · {diff.removed.length} removed · {diff.changed.length} weight changes over{" "}
        {pctText(diff.threshold, 2)} · turnover {pctText(diff.turnover, 4)}</p>
      <ul className="text-text-muted">
        {diff.added.map((a) => <li key={`a${a.key}`}>Added {a.label} at {pctText(a.weight, 4)}</li>)}
        {diff.removed.map((a) => <li key={`r${a.key}`}>Removed {a.label} (was {pctText(a.weight, 4)})</li>)}
        {diff.changed.map((a) => <li key={`c${a.key}`}>{a.label}: {pctText(a.from, 4)} → {pctText(a.to, 4)}</li>)}
      </ul>
    </div>
  );
}

function EditRow({ row, onSave, onRemove, onCancel }) {
  const [f, setF] = useState({
    raw_ticker: row?.raw_ticker || "", raw_cusip: row?.raw_cusip || "", raw_isin: row?.raw_isin || "",
    weight_pct: row ? pctText(row.target_weight, 6).replace("%", "") : "",
    drift_min_pct: row?.drift_min != null ? pctText(row.drift_min, 6).replace("%", "") : "",
    drift_max_pct: row?.drift_max != null ? pctText(row.drift_max, 6).replace("%", "") : "",
    sleeve_label: row?.sleeve_label || "",
  });
  const set = (k) => (e) => setF({ ...f, [k]: e.target.value });
  return (
    <div className={`${card} flex flex-wrap items-end gap-2 p-4 text-sm`}>
      {[["raw_ticker", "Ticker"], ["raw_cusip", "CUSIP"], ["raw_isin", "ISIN"], ["weight_pct", "Target %"],
        ["drift_min_pct", "Min %"], ["drift_max_pct", "Max %"], ["sleeve_label", "Sleeve"]].map(([k, label]) => (
        <label key={k} className="flex flex-col gap-1">{label}<input className={`${input} w-28`} value={f[k]} onChange={set(k)} /></label>
      ))}
      <button type="button" className={primary} onClick={() => onSave({
        ...f, drift_min_pct: f.drift_min_pct || null, drift_max_pct: f.drift_max_pct || null,
      })}>Save row</button>
      {row ? <button type="button" className={secondary} onClick={onRemove}>Remove row</button> : null}
      <button type="button" className="text-navy underline" onClick={onCancel}>Cancel</button>
    </div>
  );
}

export function SleevesEditor({ envelope, model, versionEnv, onSaved }) {
  const v = vocab(envelope);
  const children = Array.isArray(envelope?.available_children) ? envelope.available_children : [];
  const start = (versionEnv?.sleeves || []).map((s) => ({
    child_model_id: s.child_model_id, weight_pct: pctText(s.weight, 6).replace("%", ""), follow: s.follow,
    pinned_version_id: s.pinned_version_id,
  }));
  const [rows, setRows] = useState(start.length ? start : [{ child_model_id: "", weight_pct: "", follow: "live" }]);
  const [msg, setMsg] = useState(null);
  const total = sumPct(rows.map((r) => pctInput(r.weight_pct) ?? "x"));
  const setRow = (i, k, val) => setRows(rows.map((r, j) => (j === i ? { ...r, [k]: val } : r)));
  async function save() {
    setMsg(null);
    try {
      await call(`${model.id}/versions/${versionEnv.version.id}/sleeves`, { sleeves: rows.filter((r) => r.child_model_id) }, "PUT");
      onSaved?.();
    } catch (e) { setMsg(e.message); }
  }
  return (
    <div className="space-y-2 text-sm">
      {rows.map((r, i) => (
        <div key={i} className="flex flex-wrap items-end gap-2">
          <select className={input} value={r.child_model_id} onChange={(e) => setRow(i, "child_model_id", e.target.value)}>
            <option value="">Choose a model</option>
            {children.map((c) => <option key={c.id} value={c.id}>{c.name} (v{c.live_version_no})</option>)}
          </select>
          <input className={`${input} w-28`} value={r.weight_pct} placeholder="Weight %" onChange={(e) => setRow(i, "weight_pct", e.target.value)} />
          <select className={input} value={r.follow} onChange={(e) => setRow(i, "follow", e.target.value)}>
            {(v.follow_modes || []).filter((f) => f.key === "live" || r.pinned_version_id).map((f) => <option key={f.key} value={f.key}>{f.label}</option>)}
          </select>
          <button type="button" className="text-navy underline" onClick={() => setRows(rows.filter((_, j) => j !== i))}>Remove</button>
        </div>
      ))}
      <div className="flex items-center gap-3">
        <button type="button" className="text-navy underline" onClick={() => setRows([...rows, { child_model_id: "", weight_pct: "", follow: "live" }])}>Add sleeve</button>
        <span className={total === "100" ? "text-[#2D6A4F]" : "text-[#9B2335]"}>Total {total ?? "—"}% (must be 100%)</span>
      </div>
      {msg ? <p className="text-[#9B2335]">{msg}</p> : null}
      <button type="button" className={primary} disabled={total !== "100"} onClick={save}>Save sleeves</button>
    </div>
  );
}

export function VersionPanel({ envelope, model, versionEnv, title, onChange }) {
  const v = vocab(envelope);
  const c = versionControls(versionEnv, envelope?.permissions);
  const ver = versionEnv?.version || {};
  const [editing, setEditing] = useState(null);
  const [note, setNote] = useState("");
  const [msg, setMsg] = useState(null);
  const [problems, setProblems] = useState([]);
  async function act(path, body) {
    setMsg(null); setProblems([]);
    try { await call(`${model.id}/versions/${ver.id}/${path}`, body); onChange?.(); } catch (e) {
      setMsg(e.message); setProblems(e.detail?.messages || []);
    }
  }
  async function saveRow(row, fields) {
    await act("positions", { changes: [{ op: "upsert", ...(row ? { row_num: row.row_num } : {}), ...fields }] });
    setEditing(null);
  }
  const totals = ver.totals || {};
  return (
    <div className={`${card} space-y-4 p-5`}>
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <p className="text-lg font-semibold text-navy">{title} — v{ver.version_no} · {labelOf(v.version_statuses, ver.status)}</p>
        <p className="text-sm text-text-muted">{labelOf(v.origins, ver.origin)}{ver.approved_at ? ` · approved ${ver.approved_at.slice(0, 10)}` : ""}</p>
      </div>
      <p className="text-sm text-text-muted">{totals.positions ?? 0} positions ({totals.unresolved ?? 0} unresolved, {totals.cash ?? 0} cash)
        {totals.sleeves ? ` · ${totals.sleeves} sleeves` : ""} · total {pctText(totals.effective_total, 4)}
        {ver.decision_note ? ` · note: ${ver.decision_note}` : ""}</p>
      {Array.isArray(versionEnv?.sleeves) && versionEnv.sleeves.length ? (
        <div className="text-sm">
          <p className="font-medium text-navy">Sleeves</p>
          <ul>{versionEnv.sleeves.map((s) => (
            <li key={s.id}>{s.child_name} — {pctText(s.weight, 4)} · {labelOf(v.follow_modes, s.follow)} (v{s.resolved_version_no})</li>
          ))}</ul>
        </div>
      ) : null}
      {c.edit && model.source === "composite" ? (
        <SleevesEditor envelope={envelope} model={model} versionEnv={versionEnv} onSaved={onChange} />
      ) : null}
      <PositionsTable positions={versionEnv?.positions} editable={c.edit} onEdit={(p) => setEditing(p)} />
      {c.edit ? (
        editing ? (
          <EditRow row={editing === "new" ? null : editing} onCancel={() => setEditing(null)}
            onSave={(f) => saveRow(editing === "new" ? null : editing, f)}
            onRemove={() => act("positions", { changes: [{ op: "remove", row_num: editing.row_num }] }).then(() => setEditing(null))} />
        ) : model.source !== "composite" ? (
          <button type="button" className="text-sm text-navy underline" onClick={() => setEditing("new")}>Add a position</button>
        ) : null
      ) : null}
      {versionEnv?.look_through ? (
        <div className="text-sm">
          <p className="font-medium text-navy">Look-through</p>
          <ul>{versionEnv.look_through.map((l) => <li key={l.key}>{l.label} — {pctText(l.weight, 4)}</li>)}</ul>
        </div>
      ) : null}
      <div>
        <p className="text-sm font-medium text-navy">Change against the version it replaces</p>
        <DiffView diff={ver.diff} />
      </div>
      {Array.isArray(ver.tradability) && ver.tradability.length ? (
        <ul className="text-xs text-text-muted">{ver.tradability.map((t, i) => <li key={i}>{t.text}</li>)}</ul>
      ) : null}
      {versionEnv?.task ? (
        <p className="text-sm text-text-muted">Task: {versionEnv.task.title} — {versionEnv.task.status}</p>
      ) : null}
      <div className="flex flex-wrap items-end gap-2 text-sm">
        {c.submit ? <button type="button" className={primary} onClick={() => act("submit")}>Submit for approval</button> : null}
        {c.approve || c.reject ? (
          <input className={input} placeholder="Note (required to reject)" value={note} onChange={(e) => setNote(e.target.value)} />
        ) : null}
        {c.approve ? <button type="button" className={primary} onClick={() => act("approve", { note: note || null })}>Approve</button> : null}
        {c.reject ? <button type="button" className={secondary} disabled={!note.trim()} onClick={() => act("reject", { note })}>Reject</button> : null}
        {c.abandon ? (
          <button type="button" className={secondary} onClick={() => {
            const reason = window.prompt("Why is this draft being abandoned?");
            if (reason) act("abandon", { reason });
          }}>Abandon draft</button>
        ) : null}
      </div>
      {msg ? <p className="text-sm text-[#9B2335]">{msg}</p> : null}
      {problems.length ? <ul className="text-sm text-[#9B2335]">{problems.map((p, i) => <li key={i}>{p.row ? `Row ${p.row}: ` : ""}{p.text}</li>)}</ul> : null}
    </div>
  );
}

export function PublishForm({ envelope, model, shelf, onDone }) {
  const v = vocab(envelope);
  const [f, setF] = useState({ title: model.name, provider: model.provider_name || "", update_cadence: "", licensing_note: "", requires_agreement: false });
  const [msg, setMsg] = useState(null);
  async function save() {
    try { await call("library", { model_id: model.id, shelf, ...f }); onDone?.(); } catch (e) { setMsg(e.message); }
  }
  return (
    <div className={`${card} space-y-2 p-4 text-sm`}>
      <p className="font-medium text-navy">Publish to the {labelOf(v.shelves, shelf).toLowerCase()}</p>
      <input className={input} value={f.title} onChange={(e) => setF({ ...f, title: e.target.value })} placeholder="Title" />
      <input className={input} value={f.provider} onChange={(e) => setF({ ...f, provider: e.target.value })} placeholder="Provider" />
      <select className={input} value={f.update_cadence} onChange={(e) => setF({ ...f, update_cadence: e.target.value })}>
        <option value="">Update cadence</option>
        {(v.cadences || []).map((c) => <option key={c.key} value={c.key}>{c.label}</option>)}
      </select>
      <input className={input} value={f.licensing_note} onChange={(e) => setF({ ...f, licensing_note: e.target.value })} placeholder="Licensing note" />
      <label className="flex items-center gap-2"><input type="checkbox" checked={f.requires_agreement}
        onChange={(e) => setF({ ...f, requires_agreement: e.target.checked })} /> Requires an agreement on file</label>
      {msg ? <p className="text-[#9B2335]">{msg}</p> : null}
      <button type="button" className={primary} disabled={!f.title || !f.provider || !f.update_cadence} onClick={save}>Publish</button>
    </div>
  );
}

export default function ModelPage({ initialEnvelope }) {
  const [env, setEnv] = useState(initialEnvelope);
  const [viewing, setViewing] = useState(null);
  const [publishing, setPublishing] = useState(null);
  const [msg, setMsg] = useState(null);
  const model = env?.model || {};
  const v = vocab(env);
  const mc = modelControls(env);

  async function reload() {
    const res = await fetch(`/api/models/${model.id}`);
    const data = await res.json().catch(() => ({}));
    if (res.ok) { setEnv(data); setPublishing(null); } else setMsg(data.error || `Reload failed (${res.status})`);
  }
  async function newDraft() {
    try { await call(`${model.id}/drafts`); await reload(); } catch (e) { setMsg(e.message); }
  }
  async function view(versionId) {
    const res = await fetch(`/api/models/${model.id}/versions/${versionId}`);
    const data = await res.json().catch(() => ({}));
    if (res.ok) setViewing(data); else setMsg(data.error || "Could not load the version");
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <div>
          <h1 className="text-3xl font-semibold text-navy">{model.name}</h1>
          <p className="mt-1 text-sm text-text-muted">{labelOf(v.sources, model.source)} · {labelOf(v.model_statuses, model.status)}
            {model.provider_name ? ` · ${model.provider_name}` : ""} · {model.base_currency}</p>
        </div>
        <div className="flex flex-wrap gap-2 text-sm">
          {mc.newDraft ? <button type="button" className={primary} onClick={newDraft}>New draft</button> : null}
          {mc.uploadVersion ? <a className={secondary} href={`/portfolio/models/upload?model=${model.id}`}>Upload a new version</a> : null}
          {mc.publishOrg ? <button type="button" className={secondary} onClick={() => setPublishing("org")}>Publish to our shelf</button> : null}
          {mc.publishPlatform ? <button type="button" className={secondary} onClick={() => setPublishing("platform")}>Publish to the platform shelf</button> : null}
        </div>
      </div>
      {msg ? <p className="text-sm text-[#9B2335]">{msg}</p> : null}
      {publishing && ((publishing === "org" && mc.publishOrg) || (publishing === "platform" && mc.publishPlatform)) ? (
        <PublishForm envelope={env} model={model} shelf={publishing} onDone={reload} />
      ) : null}
      {env?.open ? <VersionPanel envelope={env} model={model} versionEnv={env.open} title="Open version" onChange={reload} /> : null}
      {env?.live ? <VersionPanel envelope={env} model={model} versionEnv={env.live} title="Approved version" onChange={reload} /> : null}
      <div className={`${card} p-5 text-sm`}>
        <p className="font-medium text-navy">Versions</p>
        <ul className="mt-2 space-y-1">{(env?.versions || []).map((ver) => (
          <li key={ver.id}>
            <button type="button" className="text-navy underline" onClick={() => view(ver.id)}>v{ver.version_no}</button>{" "}
            {labelOf(v.version_statuses, ver.status)} · {labelOf(v.origins, ver.origin)} · {String(ver.created_at || "").slice(0, 10)}
          </li>
        ))}</ul>
        {viewing ? <div className="mt-4"><VersionPanel envelope={{ ...env, permissions: {} }} model={model} versionEnv={viewing}
          title="Viewing" /></div> : null}
      </div>
      {env?.subscription ? (
        <p className="text-sm text-text-muted">Subscribed to a library entry: each new approved version arrives here as a draft
          with a review task.</p>
      ) : null}
      <div className={`${card} p-5 text-sm`}>
        <p className="font-medium text-navy">Upload history</p>
        <ul className="mt-2">{(env?.uploads || []).map((u) => (
          <li key={u.id}>{String(u.uploaded_at || "").slice(0, 16).replace("T", " ")} — {u.file_name} · {labelOf(v.upload_outcomes, u.outcome)}</li>
        ))}</ul>
      </div>
      <div className={`${card} p-5 text-sm`}>
        <p className="font-medium text-navy">Log</p>
        <ul className="mt-2">{(env?.events || []).map((e, i) => (
          <li key={i}>{String(e.created_at || "").slice(0, 16).replace("T", " ")} — {labelOf(v.events, e.action)}{e.actor_name ? ` · ${e.actor_name}` : ""}</li>
        ))}</ul>
      </div>
    </div>
  );
}
