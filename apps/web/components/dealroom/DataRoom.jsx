"use client";

/**
 * DataRoom — a deal's data room (DEALROOM1).
 *
 * Investor path: Request access → choose investing entity → eligibility result
 * → read and sign each agreement in order (typed full name + consent) → the
 * access result. Folders render with a lock and the server's reason when not
 * open; a closed folder carries no file names (the server sends none).
 *
 * Publisher / delegate / staff controls render ONLY when the server's envelope
 * says `permissions.can_write === true` (lib/dealRoom.canWrite) — no fallback.
 * Every label comes from the envelope's vocabularies (config). The browser only
 * ever calls /api/deal-room/* (the Next proxy), never FastAPI.
 */

import { useState } from "react";
import { canWrite, folderRows, investorStep } from "@/lib/dealRoom.mjs";

const CARD = "rounded-[6px] border border-[#ece8dd] bg-white";

async function call(path, { method = "GET", body, form } = {}) {
  const res = await fetch(`/api/deal-room/${path}`, {
    method,
    headers: body !== undefined ? { "Content-Type": "application/json" } : undefined,
    body: form || (body !== undefined ? JSON.stringify(body) : undefined),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = new Error(data.error || `Request failed (${res.status})`);
    err.detail = data.detail;
    throw err;
  }
  return data;
}

function LockIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" aria-hidden="true" className="inline-block text-text-muted">
      <path fill="none" stroke="currentColor" strokeWidth="1.8" d="M6 11h12v9H6zM8.5 11V8a3.5 3.5 0 0 1 7 0v3" />
    </svg>
  );
}

export function FolderList({ envelope, manage, levels, onChange, setError }) {
  const rows = folderRows(envelope);
  const dealId = envelope?.deal?.id;
  const run = async (fn) => {
    try {
      setError?.(null);
      await fn();
      await onChange?.();
    } catch (e) {
      setError?.(e.message);
    }
  };
  return (
    <ul className="divide-y divide-[#ece8dd]" data-testid="folders">
      {rows.map((f) => (
        <li key={f.id} className="py-3" style={{ paddingLeft: `${f.depth * 20}px` }} data-folder={f.label}>
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="text-sm font-medium text-navy">
              {!f.open && <LockIcon />} {f.label}
              <span className="ml-2 text-xs font-normal text-text-muted">{f.levelLabel}</span>
            </div>
            {manage && (
              <div className="flex items-center gap-2">
                <select
                  aria-label={`Access level for ${f.label}`}
                  className="rounded border border-border bg-white px-2 py-1 text-xs"
                  defaultValue={envelope.folders.find((x) => x.id === f.id)?.access_level}
                  onChange={(e) => run(() => call(`deals/${dealId}/folders/${f.id}`, {
                    method: "PATCH", body: { access_level: e.target.value } }))}
                >
                  {levels.map((l) => <option key={l.key} value={l.key}>{l.label}</option>)}
                </select>
                <label className="cursor-pointer text-xs font-medium text-navy hover:underline">
                  Upload
                  <input type="file" className="hidden" onChange={(e) => {
                    const file = e.target.files?.[0];
                    if (!file) return;
                    const form = new FormData();
                    form.append("file", file, file.name);
                    run(() => call(`deals/${dealId}/folders/${f.id}/documents`, { method: "POST", form }));
                  }} />
                </label>
                <button type="button" className="text-xs text-navy hover:underline" onClick={() => {
                  const label = window.prompt("Subfolder name");
                  if (label) run(() => call(`deals/${dealId}/folders`, { method: "POST",
                    body: { parent_id: f.id, label, access_level: envelope.folders.find((x) => x.id === f.id)?.access_level } }));
                }}>Add subfolder</button>
              </div>
            )}
          </div>
          {!f.open && f.lockReason && <p className="mt-1 text-xs text-text-muted">{f.lockReason}</p>}
          {f.open && (
            <ul className="mt-2 space-y-1">
              {f.documents.length === 0 && <li className="text-xs text-text-muted">No documents yet.</li>}
              {f.documents.map((d) => (
                <li key={d.id} className="flex items-center justify-between text-sm text-text-secondary">
                  <button type="button" className="text-left hover:text-navy hover:underline" onClick={() =>
                    run(async () => {
                      const { url } = await call(`deals/${dealId}/documents/${d.id}/download`);
                      window.open(url, "_blank", "noopener");
                    })}>{d.file_name}</button>
                  {manage && (
                    <button type="button" className="text-xs text-[#9B2335] hover:underline"
                      onClick={() => run(() => call(`deals/${dealId}/documents/${d.id}`, { method: "DELETE" }))}>
                      Remove
                    </button>
                  )}
                </li>
              ))}
            </ul>
          )}
        </li>
      ))}
    </ul>
  );
}

export function InvestorPanel({ envelope, onChange }) {
  const [entityId, setEntityId] = useState(envelope?.investing_entities?.[0]?.id || "");
  const [doc, setDoc] = useState(null);
  const [name, setName] = useState("");
  const [consent, setConsent] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const state = investorStep(envelope);
  const dealId = envelope?.deal?.id;
  const access = envelope?.my_access;

  const act = async (fn) => {
    setBusy(true);
    setError(null);
    try {
      await fn();
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <section className={`${CARD} p-5`} data-step={state.step}>
      <h3 className="text-base font-semibold text-navy">Data room access</h3>
      {access?.status_label && <p className="mt-1 text-sm text-text-secondary">{access.status_label}
        {access.entity_name ? ` · ${access.entity_name}` : ""}</p>}

      {state.step === "request" && (
        <div className="mt-3 space-y-3">
          {(envelope.investing_entities || []).length === 0 ? (
            <p className="text-sm text-text-muted">No investing entity is linked to your account yet.</p>
          ) : (
            <>
              <label className="block text-sm text-text-secondary">
                Investing through
                <select className="mt-1 block w-full rounded border border-border bg-white px-2 py-1.5 text-sm"
                  value={entityId} onChange={(e) => setEntityId(e.target.value)}>
                  {envelope.investing_entities.map((e) => <option key={e.id} value={e.id}>{e.name}</option>)}
                </select>
              </label>
              <button type="button" disabled={busy || !entityId}
                className="rounded bg-navy px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
                onClick={() => act(async () => {
                  await call(`deals/${dealId}/access/request`, { method: "POST", body: { entity_id: entityId } });
                  await onChange?.();
                })}>Request access</button>
            </>
          )}
        </div>
      )}

      {state.step === "not_eligible" && (
        <div className="mt-3 text-sm text-text-secondary">
          <p>This deal&rsquo;s eligibility rule is not yet met. What is missing:</p>
          <ul className="mt-2 list-disc pl-5">{state.missing.map((m) => <li key={m}>{m}</li>)}</ul>
          <p className="mt-2 text-text-muted">Our team has been notified and will follow up.</p>
        </div>
      )}

      {state.step === "sign" && (
        <div className="mt-3 space-y-3 text-sm">
          <p className="text-text-secondary">
            {state.remaining} agreement{state.remaining > 1 ? "s" : ""} to sign, in order. Next: <strong>{state.next.title}</strong>
          </p>
          {!doc && (
            <button type="button" disabled={busy}
              className="rounded border border-navy px-4 py-2 text-sm font-medium text-navy"
              onClick={() => act(async () => setDoc(await call(`deals/${dealId}/agreements/${state.next.id}/render`)))}>
              Read the agreement
            </button>
          )}
          {doc && (
            <>
              {doc.template_status === "draft" && (
                <p className="text-xs text-[#9B2335]">Template marked DRAFT — pending counsel review.</p>
              )}
              <pre className="max-h-96 overflow-auto whitespace-pre-wrap rounded border border-border bg-bg-app p-4 font-sans text-sm text-text-primary">
                {doc.text}
              </pre>
              <label className="block text-text-secondary">
                Type your full name
                <input className="mt-1 block w-full rounded border border-border px-2 py-1.5" value={name}
                  onChange={(e) => setName(e.target.value)} />
              </label>
              <label className="flex items-center gap-2 text-text-secondary">
                <input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} />
                I agree to sign electronically
              </label>
              <button type="button" disabled={busy || !consent || name.trim().length < 2}
                className="rounded bg-navy px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
                onClick={() => act(async () => {
                  await call(`deals/${dealId}/agreements/${state.next.id}/sign`, { method: "POST", body: {
                    typed_name: name, consent, rendered_text_sha256: doc.rendered_text_sha256 } });
                  setDoc(null); setName(""); setConsent(false);
                  await onChange?.();
                })}>Sign</button>
            </>
          )}
        </div>
      )}

      {state.step === "awaiting" && (
        <p className="mt-3 text-sm text-text-secondary">You have signed every agreement. The publisher will review your request.</p>
      )}
      {state.step === "granted" && (
        <p className="mt-3 text-sm text-[#2D6A4F]">Your access is open. Folders that need more show why below.</p>
      )}
      {(state.step === "declined" || state.step === "revoked") && (
        <p className="mt-3 text-sm text-text-secondary">{access?.reason || ""}</p>
      )}

      {(envelope?.agreements || []).some((a) => a.signature_id) && (
        <ul className="mt-4 space-y-1 border-t border-border pt-3 text-xs text-text-muted">
          {envelope.agreements.filter((a) => a.signature_id).map((a) => (
            <li key={a.id}>
              {a.title} — signed {a.signed_at.slice(0, 10)} ·{" "}
              <a className="text-navy hover:underline" href={`/api/deal-room/signatures/${a.signature_id}/copy`}
                target="_blank" rel="noopener">signed copy</a>
            </li>
          ))}
        </ul>
      )}
      {error && <p className="mt-3 text-sm text-[#9B2335]" role="alert">{error}</p>}
    </section>
  );
}

function ManagerPanel({ envelope, templates, onChange, setError }) {
  const v = envelope.vocabularies || {};
  const s = envelope.settings || {};
  const dealId = envelope.deal.id;
  const [form, setForm] = useState({
    eligibility_rule: s.eligibility_rule, approval_mode: s.approval_mode,
    sponsor_display: s.sponsor_display, code_name: s.code_name || "",
  });
  const [delegate, setDelegate] = useState("");
  const run = async (fn) => {
    try {
      setError(null);
      await fn();
      await onChange();
    } catch (e) {
      setError(e.message);
    }
  };
  const sel = (key, options) => (
    <select className="mt-1 block w-full rounded border border-border bg-white px-2 py-1.5 text-sm"
      value={form[key] || ""} onChange={(e) => setForm({ ...form, [key]: e.target.value })}>
      {(options || []).map((o) => <option key={o.key} value={o.key}>{o.label}</option>)}
    </select>
  );
  return (
    <div className="space-y-6" data-testid="manager-panel">
      <section className={`${CARD} p-5`}>
        <h3 className="text-base font-semibold text-navy">Room settings</h3>
        <div className="mt-3 grid gap-3 sm:grid-cols-2 text-sm text-text-secondary">
          <label>Eligibility{sel("eligibility_rule", v.eligibility_rules)}</label>
          <label>Approval{sel("approval_mode", v.approval_modes)}</label>
          <label>Teaser sponsor{sel("sponsor_display", v.sponsor_display)}</label>
          {form.sponsor_display === "code_name" && (
            <label>Code name
              <input className="mt-1 block w-full rounded border border-border px-2 py-1.5" value={form.code_name}
                onChange={(e) => setForm({ ...form, code_name: e.target.value })} />
            </label>
          )}
        </div>
        <button type="button" className="mt-3 rounded bg-navy px-4 py-2 text-sm font-medium text-white"
          onClick={() => run(() => call(`deals/${dealId}/settings`, { method: "PUT", body: form }))}>Save settings</button>
      </section>

      <section className={`${CARD} p-5`}>
        <h3 className="text-base font-semibold text-navy">Agreements</h3>
        <ol className="mt-3 space-y-3 text-sm">
          {(envelope.agreements || []).map((a) => (
            <li key={a.id} className="rounded border border-border p-3">
              <div className="flex items-center justify-between">
                <span className="font-medium text-navy">{a.sort_order}. {a.title} <span className="text-xs text-text-muted">v{a.template_version} · {a.status}</span></span>
                {a.status === "draft" && (
                  <button type="button" className="text-xs font-medium text-navy hover:underline"
                    onClick={() => run(() => call(`deals/${dealId}/agreements/${a.id}/publish`, { method: "POST" }))}>
                    Publish
                  </button>
                )}
              </div>
              {a.status === "draft" && (a.fields || []).map((f) => (
                <label key={f.key} className="mt-2 block text-xs text-text-secondary">
                  {f.label}
                  <input className="mt-1 block w-full rounded border border-border px-2 py-1 text-sm"
                    defaultValue={a.field_values?.[f.key] ?? f.default ?? ""}
                    onBlur={(e) => run(() => call(`deals/${dealId}/agreements/${a.id}`, {
                      method: "PATCH", body: { field_values: { [f.key]: f.type === "flag"
                        ? ["on", "true", "yes", "1"].includes(e.target.value.toLowerCase()) : e.target.value } } }))} />
                </label>
              ))}
              {a.status === "draft" && (a.blockers || []).length > 0 && (
                <p className="mt-2 text-xs text-[#9B2335]">Missing before publishing: {a.blockers.map((b) => b.label).join(", ")}</p>
              )}
            </li>
          ))}
        </ol>
        <div className="mt-3 flex flex-wrap gap-2">
          {(templates || []).map((t) => (
            <button key={t.id} type="button" className="rounded border border-navy px-3 py-1.5 text-xs font-medium text-navy"
              onClick={() => run(() => call(`deals/${dealId}/agreements`, { method: "POST", body: { template_id: t.id } }))}>
              Require {t.title} (v{t.version}{t.status === "draft" ? ", DRAFT" : ""})
            </button>
          ))}
        </div>
      </section>

      <section className={`${CARD} p-5`}>
        <h3 className="text-base font-semibold text-navy">Delegates</h3>
        <ul className="mt-2 text-sm text-text-secondary">
          {(envelope.delegates || []).map((d) => (
            <li key={d.user_id} className="flex justify-between">{d.name}
              <button type="button" className="text-xs text-[#9B2335] hover:underline"
                onClick={() => run(() => call(`deals/${dealId}/delegates/${d.user_id}`, { method: "DELETE" }))}>Remove</button>
            </li>
          ))}
        </ul>
        <div className="mt-2 flex gap-2">
          <input className="flex-1 rounded border border-border px-2 py-1.5 text-sm" placeholder="User id"
            value={delegate} onChange={(e) => setDelegate(e.target.value)} />
          <button type="button" className="rounded bg-navy px-3 py-1.5 text-sm text-white"
            onClick={() => run(() => call(`deals/${dealId}/delegates`, { method: "POST", body: { user_id: delegate } }))}>Add</button>
        </div>
        <a href="/marketplace/access" className="mt-3 inline-block text-sm text-navy hover:underline">Deal access grid →</a>
      </section>
    </div>
  );
}

export default function DataRoom({ initialEnvelope, templates = [] }) {
  const [envelope, setEnvelope] = useState(initialEnvelope);
  const [error, setError] = useState(null);
  if (!envelope) {
    return <p className="text-sm text-text-muted">The data room is not available.</p>;
  }
  const manage = canWrite(envelope);
  const refresh = async () => setEnvelope(await call(`deals/${envelope.deal.id}`));
  return (
    <div className="space-y-6">
      {!manage && <InvestorPanel envelope={envelope} onChange={refresh} />}
      <section className={`${CARD} p-5`}>
        <h3 className="text-base font-semibold text-navy">Data room</h3>
        <FolderList envelope={envelope} manage={manage} levels={envelope.vocabularies?.levels || []}
          onChange={refresh} setError={setError} />
      </section>
      {manage && <ManagerPanel envelope={envelope} templates={templates} onChange={refresh} setError={setError} />}
      {error && <p className="text-sm text-[#9B2335]" role="alert">{error}</p>}
    </div>
  );
}
