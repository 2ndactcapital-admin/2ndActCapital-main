"use client";

/**
 * Disclosures — Admin → Disclosures (DISCLOSURES1), and the household / account panel. One disclosure library for
 * the whole platform. The list shows every key, its audience and what is in force for this org and why (platform
 * default, org override, or the built-in text). Each key shows its versions (status, dates, maker / checker, the
 * stored file, the summary). Upload a new version (a draft) → a SECOND person publishes it. Retire with a reason.
 * Every control renders only from the server's envelope (lib/disclosures.mjs) and fails closed.
 */

import { useState } from "react";
import { fileAccepted, inForceText, labelOf, rowsOf, summaryOf, uploadBody, uploadControls, versionControls, vocab }
  from "@/lib/disclosures.mjs";

const card = "rounded-[6px] border border-[#ece8dd] bg-white p-4";
const input = "rounded border border-[#E2E8F0] px-3 py-2";
const primary = "rounded bg-navy px-3 py-1 text-white disabled:opacity-50";
const secondary = "rounded border border-navy px-3 py-1 text-navy disabled:opacity-50";

export function DisclosureList({ envelope, onOpen }) {
  const v = vocab(envelope);
  const rows = rowsOf(envelope);
  if (!rows.length) return <p className={`${card} text-sm text-text-muted`}>No disclosures.</p>;
  return (
    <table className="w-full text-sm" data-testid="disclosure-list">
      <thead><tr className="text-left text-text-muted"><th>Disclosure</th><th>Audience</th><th>In force for this org</th></tr></thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.key} className="border-t border-[#ece8dd] align-top">
            <td className="py-2">
              <button type="button" className="text-navy underline" onClick={() => onOpen?.(r.key)}>{r.title}</button>
              <div className="text-xs text-text-muted">{r.key}</div>
            </td>
            <td className="py-2">{labelOf(v.audiences, r.audience)}</td>
            <td className="py-2"><span data-testid="why">{inForceText(r)}</span>
              {summaryOf(r) ? <p className="mt-1 whitespace-pre-wrap text-xs text-text-muted">{summaryOf(r)}</p> : null}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export function VersionRow({ envelope, version, onPublish, onRetire }) {
  const v = vocab(envelope);
  const c = versionControls(envelope, version);
  const [reason, setReason] = useState("");
  return (
    <div className={`${card} text-sm`} data-testid="version">
      <p className="font-medium text-navy">
        {labelOf(v.scopes, version.scope)} · version {version.version_no} — {labelOf(v.statuses, version.status)}
        {version.in_force ? " (in force)" : ""}
      </p>
      <p className="mt-1 text-text-muted">
        Effective {version.effective_from}{version.effective_to ? ` until ${version.effective_to}` : ""} ·
        drafted by {version.drafted_by_name || version.drafted_by}
        {version.published_by ? ` · published by ${version.published_by_name || version.published_by}` : ""}
      </p>
      <p className="mt-1"><a className="text-navy underline" href={version.file_href}>{version.file_name}</a>
        <span className="text-xs text-text-muted"> · SHA-256 {String(version.content_sha256 || "").slice(0, 12)}…</span></p>
      <p className="mt-1 whitespace-pre-wrap">{version.summary}</p>
      {c.publish || c.retire ? (
        <div className="mt-2 flex flex-wrap gap-2">
          {c.publish ? <button type="button" className={primary} onClick={() => onPublish?.(version)}>Publish</button> : null}
          {c.retire ? (
            <>
              <input className={input} placeholder="Reason (required to retire)" value={reason} onChange={(e) => setReason(e.target.value)} />
              <button type="button" className={secondary} disabled={!reason.trim()} onClick={() => onRetire?.(version, reason)}>Retire</button>
            </>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

export function UploadForm({ envelope, fixedScope = null, recordId = null, onUpload }) {
  const c = uploadControls(envelope);
  const v = vocab(envelope);
  const scopes = fixedScope ? c.scopes.filter((s) => s === fixedScope) : c.scopes;
  const [f, setF] = useState({ scope: scopes[0] || "", summary: "", effective_from: "", note: "" });
  const [file, setFile] = useState(null);
  if (!c.upload || !scopes.length) return null;
  const ok = file && fileAccepted(envelope, file.name, file.size) && f.summary.trim() && f.scope;
  return (
    <div className={`${card} space-y-2 text-sm`} data-testid="upload-form">
      <h3 className="font-serif text-lg text-navy">Upload a new version (draft)</h3>
      <p className="text-xs text-text-muted">A second person publishes it. Files: {(v.file_types || []).join(", ")}.</p>
      <div className="flex flex-wrap gap-2">
        {fixedScope ? null : (
          <select className={input} value={f.scope} onChange={(e) => setF({ ...f, scope: e.target.value })}>
            {scopes.filter((s) => s === "platform" || s === "org").map((s) => <option key={s} value={s}>{labelOf(v.scopes, s)}</option>)}
          </select>
        )}
        <input type="file" onChange={(e) => setFile(e.target.files?.[0] || null)} />
        <input className={input} type="date" value={f.effective_from} onChange={(e) => setF({ ...f, effective_from: e.target.value })} />
      </div>
      <textarea className={`${input} h-24 w-full`} placeholder="Plain-text summary shown inline on screens" value={f.summary} onChange={(e) => setF({ ...f, summary: e.target.value })} />
      <input className={`${input} w-full`} placeholder="Note (optional)" value={f.note} onChange={(e) => setF({ ...f, note: e.target.value })} />
      <button type="button" className={primary} disabled={!ok} onClick={() => onUpload?.({ ...f, scope: fixedScope || f.scope, recordId }, file)}>Upload draft</button>
    </div>
  );
}

export function KeyView({ envelope, onPublish, onRetire, onUpload }) {
  const d = envelope?.disclosure;
  if (!d) return null;
  const versions = Array.isArray(envelope?.versions) ? envelope.versions : [];
  return (
    <div className="space-y-3" data-testid="key-view">
      <h2 className="font-serif text-2xl text-navy">{d.title}</h2>
      <p className="text-sm text-text-muted">{d.description}</p>
      <p className={`${card} text-sm`}>In force: {inForceText(envelope)}</p>
      <UploadForm envelope={envelope} onUpload={onUpload} />
      {versions.map((x) => <VersionRow key={x.id} envelope={envelope} version={x} onPublish={onPublish} onRetire={onRetire} />)}
    </div>
  );
}

function toBase64(file) {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(String(r.result).split(",").pop());
    r.onerror = () => reject(r.error);
    r.readAsDataURL(file);
  });
}

async function call(path, method = "GET", body) {
  const res = await fetch(`/api/disclosures/${path}`, {
    method, headers: body ? { "Content-Type": "application/json" } : undefined, body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || "Request failed");
  return data;
}

function useActions(reload, setMsg) {
  const run = async (fn) => { setMsg(null); try { await fn(); await reload(); } catch (e) { setMsg(e.message); } };
  return {
    publish: (v) => run(() => call(`versions/${v.id}/publish`, "POST", {})),
    retire: (v, reason) => run(() => call(`versions/${v.id}/retire`, "POST", { reason })),
    upload: (key, f, file, extra = {}) => run(async () => call(`keys/${encodeURIComponent(key)}/versions`, "POST",
      uploadBody({ ...f, ...extra, file_name: file.name, content_base64: await toBase64(file) }))),
  };
}

export function RecordDisclosuresView({ envelope, onUpload, onPublish, onRetire }) {
  const v = vocab(envelope);
  const rec = envelope?.record;
  if (!rec) return null;
  return (
    <div className="space-y-3" data-testid="record-disclosures">
      {rowsOf(envelope).map((r) => (
        <div key={r.key} className={`${card} text-sm`}>
          <p className="font-medium text-navy">{r.title} <span className="text-xs text-text-muted">· {labelOf(v.audiences, r.audience)}</span></p>
          <p className="mt-1 text-text-muted" data-testid="why">{inForceText(r)}</p>
          {(r.overrides || []).map((x) => <VersionRow key={x.id} envelope={envelope} version={x} onPublish={onPublish} onRetire={onRetire} />)}
          <UploadForm envelope={envelope} fixedScope={rec.type} recordId={rec.id} onUpload={(f, file) => onUpload?.(r.key, f, file)} />
        </div>
      ))}
    </div>
  );
}

export function RecordDisclosures({ kind, id, initialEnvelope }) {
  const [env, setEnv] = useState(initialEnvelope);
  const [msg, setMsg] = useState(null);
  const reload = async () => setEnv(await call(`${kind === "household" ? "households" : "accounts"}/${encodeURIComponent(id)}`));
  const a = useActions(reload, setMsg);
  const extra = kind === "household" ? { household_id: id } : { account_id: id };
  return (
    <div className="space-y-2">
      {msg ? <p className="text-sm text-[#9B2335]">{msg}</p> : null}
      <RecordDisclosuresView envelope={env} onPublish={a.publish} onRetire={a.retire}
        onUpload={(key, f, file) => a.upload(key, f, file, extra)} />
    </div>
  );
}

export default function DisclosuresAdmin({ initialEnvelope }) {
  const [env, setEnv] = useState(initialEnvelope);
  const [key, setKey] = useState(null);
  const [detail, setDetail] = useState(null);
  const [msg, setMsg] = useState(null);
  const open = async (k) => { setMsg(null); setKey(k); try { setDetail(await call(`keys/${encodeURIComponent(k)}`)); } catch (e) { setMsg(e.message); } };
  const reload = async () => { setEnv(await call("")); if (key) setDetail(await call(`keys/${encodeURIComponent(key)}`)); };
  const a = useActions(reload, setMsg);
  return (
    <div className="space-y-4">
      {msg ? <p className="text-sm text-[#9B2335]">{msg}</p> : null}
      <div className={card}><DisclosureList envelope={env} onOpen={open} /></div>
      {detail ? <KeyView envelope={detail} onPublish={a.publish} onRetire={a.retire} onUpload={(f, file) => a.upload(key, f, file)} /> : null}
    </div>
  );
}
