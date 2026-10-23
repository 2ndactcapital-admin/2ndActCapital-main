"use client";

/**
 * EntryView — one Replication Studio registry entry (REP1): Terms, Holdings,
 * Sources — and (REP4) Model: viability, cost comparison, find-or-build.
 *
 * Terms: every field with its value, origin chip (from the envelope's
 * `origins` vocabulary), source quote + document link, confidence and the
 * needs-review marker. Correct (the read was wrong) and Change (a deliberate
 * departure, reason required) render only from `fieldControls(envelope, key)`;
 * Approve / Reject only from `approvalControls(envelope)` — which needs
 * can_approve AND the server's own "not a maker of this draft" flag.
 * Departures are listed at the top in plain words. Nothing renders a write
 * control on a missing or partial envelope.
 */

import { useState } from "react";
import {
  approvalControls, departureLines, diffSummary, entryControls, fieldControls, formatValue, formatWeight, labelOf,
} from "@/lib/replication.mjs";
import ModelTab from "@/components/replication/ModelTab";

const card = "rounded-[6px] border border-[#ece8dd] bg-white";
const chipTone = { extracted: "bg-[#F5F1EB] text-[#334155]", corrected: "bg-[#E8D5A3] text-[#1B2B4B]",
  deliberate: "bg-[#1B2B4B] text-white" };

async function post(path, body) {
  const res = await fetch(`/api/replication/${path}`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `Request failed (${res.status})`);
  return data;
}

function FieldEditor({ mode, field, onSubmit, onCancel }) {
  const [value, setValue] = useState(field.value ?? "");
  const [text, setText] = useState("");
  const enumValues = Array.isArray(field.enum_values) ? field.enum_values : null;
  return (
    <form className="mt-2 flex flex-wrap items-end gap-2 text-sm"
      onSubmit={(e) => { e.preventDefault(); onSubmit(value, text); }}>
      {field.data_type === "boolean" ? (
        <select className="rounded border border-[#E2E8F0] px-2 py-1" value={String(value)} onChange={(e) => setValue(e.target.value)}>
          <option value="true">Yes</option><option value="false">No</option>
        </select>
      ) : enumValues ? (
        <select className="rounded border border-[#E2E8F0] px-2 py-1" value={value} onChange={(e) => setValue(e.target.value)}>
          {enumValues.map((v) => <option key={v} value={v}>{v}</option>)}
        </select>
      ) : (
        <input className="rounded border border-[#E2E8F0] px-2 py-1" value={value}
          type={field.data_type === "date" ? "date" : "text"} onChange={(e) => setValue(e.target.value)} />
      )}
      <input className="min-w-[16rem] flex-1 rounded border border-[#E2E8F0] px-2 py-1" value={text}
        placeholder={mode === "change" ? "Reason for departing from the fund (required)" : "Note (optional)"}
        onChange={(e) => setText(e.target.value)} />
      <button type="submit" disabled={mode === "change" && !text.trim()}
        className="rounded bg-navy px-3 py-1 text-white disabled:opacity-50">
        {mode === "change" ? "Save change" : "Save correction"}
      </button>
      <button type="button" onClick={onCancel} className="px-2 py-1 text-text-muted">Cancel</button>
    </form>
  );
}

export function TermsPanel({ envelope, onChanged }) {
  const terms = envelope?.terms || {};
  const v = envelope?.vocabularies || {};
  const fields = Array.isArray(terms.fields) ? terms.fields : [];
  const approval = approvalControls(envelope);
  const [editing, setEditing] = useState(null);
  const [note, setNote] = useState("");
  const [msg, setMsg] = useState(null);
  const entryId = envelope?.entry?.id;
  const shown = terms.shown_version;

  async function act(fn) {
    setMsg(null);
    try { await fn(); setEditing(null); onChanged?.(); } catch (err) { setMsg(err.message); }
  }

  return (
    <div className="space-y-4">
      {departureLines(terms.departures).length ? (
        <div className={`${card} p-4 text-sm`}>
          {departureLines(terms.departures).map((line) => <p key={line} className="text-navy">{line}</p>)}
        </div>
      ) : null}
      <div className="flex flex-wrap items-center justify-between gap-3 text-sm">
        <p className="text-text-muted">
          {shown ? `${labelOf(v.version_statuses, shown.status)} v${shown.version_no}` : "No terms yet"}
          {shown?.needs_review_fields?.length ? ` — ${shown.needs_review_fields.length} field(s) need review` : ""}
          {shown?.extraction?.status && shown.extraction.status !== "extracted" && shown.extraction.status !== "reused"
            ? ` — extraction ${shown.extraction.status}${shown.extraction.reason ? `: ${shown.extraction.reason}` : ""}` : ""}
        </p>
        {approval.approve ? (
          <div className="flex items-center gap-2">
            <input className="rounded border border-[#E2E8F0] px-2 py-1" placeholder="Decision note" value={note}
              onChange={(e) => setNote(e.target.value)} />
            <button type="button" className="rounded bg-[#2D6A4F] px-3 py-1 text-white"
              onClick={() => act(() => post(`registry/${entryId}/terms/${terms.draft_version_id}/approve`, { note: note || null }))}>
              Approve
            </button>
            <button type="button" className="rounded border border-[#9B2335] px-3 py-1 text-[#9B2335]"
              onClick={() => act(() => post(`registry/${entryId}/terms/${terms.draft_version_id}/reject`, { note: note || null }))}>
              Reject
            </button>
          </div>
        ) : null}
      </div>
      {msg ? <p className="text-sm text-[#9B2335]">{msg}</p> : null}
      <div className={`${card} divide-y divide-[#ece8dd]`}>
        {fields.map((f) => {
          const c = fieldControls(envelope, f.field_key);
          return (
            <div key={f.field_key} className="p-4 text-sm">
              <div className="flex flex-wrap items-baseline gap-3">
                <span className="w-56 font-medium text-navy">{f.label}</span>
                <span className="flex-1">{formatValue(f.value)}</span>
                {f.origin ? (
                  <span className={`rounded px-2 py-0.5 text-xs ${chipTone[f.origin] || ""}`}>{labelOf(v.origins, f.origin)}</span>
                ) : null}
                {f.needs_review ? <span className="text-xs text-[#9B2335]">Needs review</span> : null}
                {f.confidence ? <span className="text-xs text-text-muted">{labelOf(v.confidence, f.confidence)}</span> : null}
                {c.correct ? <button type="button" className="text-xs text-navy underline" onClick={() => setEditing({ key: f.field_key, mode: "correct" })}>Correct</button> : null}
                {c.change ? <button type="button" className="text-xs text-navy underline" onClick={() => setEditing({ key: f.field_key, mode: "change" })}>Change</button> : null}
              </div>
              {f.quote ? (
                <p className="mt-1 pl-56 text-xs text-text-muted">
                  &ldquo;{f.quote}&rdquo;{f.quote_verified === false ? " (not found in the document)" : ""}
                  {f.document_url ? <> — <a className="underline" href={f.document_url} target="_blank" rel="noreferrer">{f.form_type || "document"}</a></> : null}
                </p>
              ) : null}
              {f.review_reasons?.length ? <p className="mt-1 pl-56 text-xs text-[#9B2335]">{f.review_reasons.join("; ")}</p> : null}
              {f.origin === "deliberate" && f.reason ? <p className="mt-1 pl-56 text-xs text-text-muted">Reason: {f.reason}</p> : null}
              {editing?.key === f.field_key ? (
                <FieldEditor mode={editing.mode} field={f} onCancel={() => setEditing(null)}
                  onSubmit={(value, text) => act(() => (editing.mode === "correct"
                    ? post(`registry/${entryId}/terms/corrections`, { version_id: terms.draft_version_id, field_key: f.field_key, value, notes: text || null })
                    : post(`registry/${entryId}/terms/changes`, { field_key: f.field_key, value, reason: text })))} />
              ) : null}
            </div>
          );
        })}
      </div>
      <div className={`${card} p-4 text-sm`}>
        <h3 className="font-semibold text-navy">Version history</h3>
        <ul className="mt-2 space-y-1">
          {(terms.versions || []).map((ver) => (
            <li key={ver.id}>v{ver.version_no} — {labelOf(v.version_statuses, ver.status)}
              {ver.departures ? ` — ${ver.departures} departure(s)` : ""}
              {ver.approved_at ? ` — approved ${ver.approved_at.slice(0, 10)}` : ""}
              {ver.rejected_at ? ` — rejected ${ver.rejected_at.slice(0, 10)}` : ""}
              {ver.decision_note ? ` — ${ver.decision_note}` : ""}</li>
          ))}
        </ul>
      </div>
    </div>
  );
}

export function HoldingsPanel({ envelope, onQuery }) {
  const h = envelope?.holdings || {};
  const snap = envelope?.snapshot;
  const rows = Array.isArray(h.rows) ? h.rows : [];
  const d = diffSummary(snap?.diff);
  const sortBy = (sort) => onQuery?.({ sort, dir: "desc" });
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3 text-sm">
        <label className="flex items-center gap-2">As of
          <select className="rounded border border-[#E2E8F0] px-2 py-1" value={snap?.id || ""}
            onChange={(e) => onQuery?.({ snapshot_id: e.target.value })}>
            {(envelope?.snapshots || []).map((s) => (
              <option key={s.id} value={s.id}>{s.as_of}{s.status === "needs_review" ? " (needs review)" : ""}</option>
            ))}
          </select>
        </label>
        {snap ? <span className="text-text-muted">{snap.row_count} rows · weights (ex cash) {formatWeight(snap.total_weight_ex_cash)}</span> : null}
        {snap?.status === "needs_review" ? <span className="text-[#9B2335]">This snapshot failed validation and is not live.</span> : null}
      </div>
      {snap && !d.first ? (
        <div className={`${card} p-4 text-sm`}>
          <h3 className="font-semibold text-navy">Changes since the previous snapshot</h3>
          <p className="mt-1 text-text-muted">{d.added.length} added · {d.removed.length} removed · {d.moves.length} weight moves · turnover {formatWeight(d.turnover)}</p>
          {d.added.map((a) => <p key={`a${a.key}`}>Added: {a.name} ({a.ticker || a.key}) {formatWeight(a.weight)}</p>)}
          {d.removed.map((a) => <p key={`r${a.key}`}>Removed: {a.name} ({a.ticker || a.key})</p>)}
          {d.moves.slice(0, 20).map((m) => <p key={`m${m.key}`}>{m.name}: {formatWeight(m.before)} → {formatWeight(m.after)}</p>)}
        </div>
      ) : null}
      <div className={`${card} overflow-x-auto`}>
        <table className="w-full text-sm">
          <thead className="border-b border-[#ece8dd] text-left text-text-muted">
            <tr>
              {[["name", "Name"], ["ticker", "Ticker"], ["cusip", "CUSIP"], ["weight", "Weight"], ["shares", "Shares"], ["resolved", "Resolved?"]].map(([k, l]) => (
                <th key={k} className="px-4 py-2 font-medium"><button type="button" onClick={() => sortBy(k)}>{l}</button></th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.row_num} className={`border-b border-[#ece8dd] last:border-0 ${r.resolution_state === "unresolved" ? "bg-[#FAF9F6]" : ""}`}>
                <td className="px-4 py-2">{r.name}</td>
                <td className="px-4 py-2">{r.ticker || "—"}</td>
                <td className="px-4 py-2">{r.cusip || "—"}</td>
                <td className="px-4 py-2">{formatWeight(r.weight)}</td>
                <td className="px-4 py-2">{r.shares ?? "—"}</td>
                <td className={`px-4 py-2 ${r.resolution_state === "unresolved" ? "text-[#9B2335]" : ""}`}>
                  {r.resolution_state === "resolved" ? `Yes (${r.resolved_via})` : r.resolution_state === "unresolved" ? "No — queued" : "n/a"}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="text-xs text-text-muted">Showing {rows.length} of {h.total || 0}.</p>
    </div>
  );
}

export function SourcesPanel({ envelope }) {
  const docs = envelope?.sources?.documents || [];
  const snaps = envelope?.snapshots || [];
  return (
    <div className="space-y-4 text-sm">
      <div className={`${card} p-4`}>
        <h3 className="font-semibold text-navy">Documents</h3>
        {docs.length ? docs.map((d) => (
          <p key={d.id} className="mt-1"><a className="underline" href={d.source_url} target="_blank" rel="noreferrer">{d.form_type} {d.accession_number}</a>
            {" "}filed {d.filing_date || "—"} · read {d.fetched_at.slice(0, 10)} · <span className="font-mono text-xs">{d.sha256.slice(0, 16)}…</span></p>
        )) : <p className="mt-1 text-text-muted">No documents read yet.</p>}
      </div>
      <div className={`${card} p-4`}>
        <h3 className="font-semibold text-navy">Holdings files</h3>
        {snaps.map((s) => (
          <p key={s.id} className="mt-1">{s.as_of} · {s.row_count} rows · fetched {s.fetched_at.slice(0, 16).replace("T", " ")}
            {" "}· <span className="font-mono text-xs">{s.file_sha256.slice(0, 16)}…</span>
            {s.source_url ? <> · <a className="underline" href={s.source_url} target="_blank" rel="noreferrer">source</a></> : null}</p>
        ))}
      </div>
    </div>
  );
}

export default function EntryView({ initialEnvelope }) {
  const [env, setEnv] = useState(initialEnvelope);
  const [tab, setTab] = useState("terms");
  const [query, setQuery] = useState({});
  const [msg, setMsg] = useState(null);
  const controls = entryControls(env);
  const entry = env?.entry || {};

  async function reload(q = query) {
    const params = new URLSearchParams();
    for (const [k, v] of Object.entries(q)) if (v) params.set(k, String(v));
    const res = await fetch(`/api/replication/registry/${entry.id}?${params}`);
    const data = await res.json().catch(() => ({}));
    if (res.ok) setEnv(data); else setMsg(data.error || `Reload failed (${res.status})`);
  }

  async function run(path) {
    setMsg(null);
    try { await post(`registry/${entry.id}/${path}`); await reload(); } catch (err) { setMsg(err.message); }
  }

  return (
    <div className="space-y-5">
      <div>
        <p className="text-xs text-text-muted">{entry.disclosure}</p>
        <div className="mt-3 flex flex-wrap items-center gap-2">
          {["terms", "holdings", "sources", "model"].map((t) => (
            <button key={t} type="button" onClick={() => setTab(t)}
              className={`rounded px-3 py-1 text-sm ${tab === t ? "bg-navy text-white" : "bg-[#F5F1EB] text-navy"}`}>
              {t === "terms" ? "Terms" : t === "holdings" ? "Holdings" : t === "sources" ? "Sources" : "Model"}
            </button>
          ))}
          <span className="flex-1" />
          {controls.refresh ? <button type="button" className="text-sm text-navy underline" onClick={() => run("holdings/refresh")}>Refresh holdings</button> : null}
          {controls.extract ? <button type="button" className="text-sm text-navy underline" onClick={() => run("terms/extract")}>Re-run extraction</button> : null}
        </div>
      </div>
      {msg ? <p className="text-sm text-[#9B2335]">{msg}</p> : null}
      {tab === "terms" ? <TermsPanel envelope={env} onChanged={() => reload()} /> : null}
      {tab === "holdings" ? <HoldingsPanel envelope={env} onQuery={(q) => { const next = { ...query, ...q }; setQuery(next); reload(next); }} /> : null}
      {tab === "sources" ? <SourcesPanel envelope={env} /> : null}
      {tab === "model" ? <ModelTab entryId={entry.id} /> : null}
    </div>
  );
}
