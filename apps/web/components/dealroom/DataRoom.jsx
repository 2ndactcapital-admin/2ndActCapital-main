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
 *
 * DEALROOM2: an uploaded PDF (and Exhibit A) is read page by page in
 * AgreementViewer and Sign stays disabled until the last page is reached;
 * a resign_required investor is shown what changed and signs the new
 * version; signed agreements list their version and a PDF copy; managers
 * upload PDFs, save new versions (keep signers / require re-signing) and set
 * the publisher's master NDA — every control behind can_write === true.
 *
 * DEALROOM3: documents carry the server's per-document flags (can_view /
 * can_download / blocked_reason): View opens the page viewer (never the
 * original); Download appears only when can_download === true — under
 * view-only it never does. Entering the room shows the activity notice;
 * expired / paused investors see why and their signatures, no folders; Q&A,
 * advisors (and an advisor's acknowledgement), a return-or-destroy notice,
 * and for managers the new settings rows and the activity view.
 */

import { useState } from "react";
import { agreementControls, canWrite, documentActions, folderRows, investorStep, noticePending, roomNotices,
  settingsRows, shareUnwatermarkedControl, signatureLine, signEnabled, signPayload } from "@/lib/dealRoom.mjs";
import AgreementViewer from "@/components/dealroom/AgreementViewer";
import DocumentViewer from "@/components/dealroom/DocumentViewer";
import QAPanel from "@/components/dealroom/QAPanel";
import ActivityPanel from "@/components/dealroom/ActivityPanel";
import { AdvisorAcknowledgement, InvestorAdvisors } from "@/components/dealroom/AdvisorsPanel";

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
  const [viewer, setViewer] = useState(null);
  const [opened, setOpened] = useState({});
  const docsOf = (f) => envelope.folders.find((x) => x.id === f.id)?.documents || [];
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
              {!f.open && <LockIcon />}{" "}
              {f.open && !manage ? (
                <button type="button" className="hover:underline" data-action="open-folder"
                  onClick={async () => {
                    // the open is recorded once per visit (the activity log; first open may notify the
                    // publisher) — no room refresh, which would log a second room open
                    if (opened[f.id]) return;
                    try {
                      await call(`deals/${dealId}/folders/${f.id}/open`, { method: "POST" });
                      setOpened((x) => ({ ...x, [f.id]: true }));
                    } catch (e) { setError?.(e.message); }
                  }}>{f.label}</button>
              ) : f.label}
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
              {docsOf(f).map((d) => {
                const acts = documentActions(d);
                return (
                  <li key={d.id} className="text-sm text-text-secondary" data-document={d.file_name}>
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <span>{d.file_name}{d.watermarked ? <span className="ml-2 text-xs text-text-muted">watermarked</span> : null}</span>
                      <span className="flex items-center gap-3 text-xs">
                        {acts.includes("view") && (
                          <button type="button" className="text-navy hover:underline" data-action="view"
                            onClick={() => run(async () => setViewer(await call(`deals/${dealId}/documents/${d.id}/pages`)))}>
                            View
                          </button>
                        )}
                        {acts.includes("download") && (
                          <button type="button" className="text-navy hover:underline" data-action="download"
                            onClick={() => run(async () => {
                              const out = await call(`deals/${dealId}/documents/${d.id}/download`);
                              window.open(out.url || out.href, "_blank", "noopener");
                            })}>Download</button>
                        )}
                        {shareUnwatermarkedControl(envelope, d) && (
                          <label className="flex items-center gap-1 text-text-muted" data-action="share-unwatermarked">
                            <input type="checkbox" defaultChecked={d.share_unwatermarked === true}
                              onChange={(e) => run(() => call(`deals/${dealId}/documents/${d.id}/share-unwatermarked`,
                                { method: "PUT", body: { share: e.target.checked } }))} />
                            Share unwatermarked
                          </label>
                        )}
                        {manage && (
                          <button type="button" className="text-[#9B2335] hover:underline"
                            onClick={() => run(() => call(`deals/${dealId}/documents/${d.id}`, { method: "DELETE" }))}>
                            Remove
                          </button>
                        )}
                      </span>
                    </div>
                    {d.blocked_reason && <p className="mt-0.5 text-xs text-text-muted" data-blocked>{d.blocked_reason}</p>}
                  </li>
                );
              })}
              {(envelope.folders.find((x) => x.id === f.id)?.answered_questions || []).map((q) => (
                <li key={q.id} className="border-l-2 border-[#C5A880] pl-3 text-sm text-text-secondary">
                  <p className="font-medium text-navy">{q.body}</p>
                  <p>{q.answer}</p>
                </li>
              ))}
            </ul>
          )}
        </li>
      ))}
      {viewer && (
        <li className="py-3">
          <DocumentViewer dealId={dealId} info={viewer} viewOnlyNotice={envelope?.notices?.view_only}
            onClose={() => setViewer(null)} />
        </li>
      )}
    </ul>
  );
}

export function InvestorPanel({ envelope, onChange }) {
  const [entityId, setEntityId] = useState(envelope?.investing_entities?.[0]?.id || "");
  const [doc, setDoc] = useState(null);
  const [name, setName] = useState("");
  const [consent, setConsent] = useState(false);
  const [scrolled, setScrolled] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const state = investorStep(envelope);
  const needsScroll = doc?.kind === "upload" || Boolean(doc?.exhibit);
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
          {state.resign && (
            <p className="text-[#9B2335]" data-testid="resign-notice">
              An agreement for this deal has changed. Sign the new version to reopen the folders you had.
            </p>
          )}
          <p className="text-text-secondary">
            {state.remaining} agreement{state.remaining > 1 ? "s" : ""} to sign, in order. Next: <strong>{state.next.title}</strong>
          </p>
          {!doc && (
            <button type="button" disabled={busy}
              className="rounded border border-navy px-4 py-2 text-sm font-medium text-navy"
              onClick={() => act(async () => {
                setScrolled(false);
                setDoc(await call(`deals/${dealId}/agreements/${state.next.id}/render`));
              })}>
              Read the agreement
            </button>
          )}
          {doc && (
            <>
              <AgreementViewer dealId={dealId} doc={doc} onScrolledToEnd={() => setScrolled(true)} />
              <label className="block text-text-secondary">
                Type your full name
                <input className="mt-1 block w-full rounded border border-border px-2 py-1.5" value={name}
                  onChange={(e) => setName(e.target.value)} />
              </label>
              <label className="flex items-center gap-2 text-text-secondary">
                <input type="checkbox" checked={consent} onChange={(e) => setConsent(e.target.checked)} />
                I agree to sign electronically
              </label>
              <button type="button" data-testid="sign-button"
                disabled={!signEnabled({ needsScroll, scrolledToEnd: scrolled, consent, name, busy })}
                className="rounded bg-navy px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
                onClick={() => act(async () => {
                  await call(`deals/${dealId}/agreements/${state.next.id}/sign`, { method: "POST",
                    body: signPayload(doc, { name, consent }) });
                  setDoc(null); setName(""); setConsent(false); setScrolled(false);
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
      {state.step === "expired" && (
        <p className="mt-3 text-sm text-text-secondary" data-testid="expired-notice">
          Your access period ended{state.endedOn ? ` on ${state.endedOn}` : ""}. Your signed agreements stay below;
          ask the publisher to extend your access.
        </p>
      )}
      {state.step === "paused" && (
        <p className="mt-3 text-sm text-[#9B2335]" data-testid="paused-notice">
          Your access is paused: {state.reason} It reopens automatically once your record is updated.
        </p>
      )}
      {state.step === "granted" && access?.access_ends_on && (
        <p className="mt-1 text-xs text-text-muted">Access until {access.access_ends_on}.</p>
      )}
      {noticePending(envelope?.return_notice) && <ReturnNotice dealId={dealId} notice={envelope.return_notice}
        onDone={onChange} />}

      {(envelope?.agreements || []).some((a) => a.signature_id) && (
        <ul className="mt-4 space-y-1 border-t border-border pt-3 text-xs text-text-muted">
          {envelope.agreements.filter((a) => a.signature_id).map((a) => (
            <li key={a.id}>
              {signatureLine(a)} ·{" "}
              <a className="text-navy hover:underline" href={`/api/deal-room/signatures/${a.signature_id}/copy`}
                target="_blank" rel="noopener">signed copy</a> ·{" "}
              <a className="text-navy hover:underline" href={`/api/deal-room/signatures/${a.signature_id}/pdf`}>PDF</a>
            </li>
          ))}
        </ul>
      )}
      {error && <p className="mt-3 text-sm text-[#9B2335]" role="alert">{error}</p>}
    </section>
  );
}

export function ReturnNotice({ dealId, notice, onDone }) {
  const [name, setName] = useState("");
  const [error, setError] = useState(null);
  return (
    <div className="mt-4 rounded border border-[#9B2335]/30 p-3 text-sm" data-testid="return-notice">
      <pre className="whitespace-pre-wrap font-sans text-text-secondary">{notice.text}</pre>
      <label className="mt-2 block text-text-secondary">Type your full name to confirm
        <input className="mt-1 block w-full rounded border border-border px-2 py-1.5" value={name}
          onChange={(e) => setName(e.target.value)} />
      </label>
      <button type="button" disabled={name.trim().length < 2}
        className="mt-2 rounded bg-navy px-4 py-2 text-sm font-medium text-white disabled:opacity-50"
        onClick={async () => {
          try {
            await call(`deals/${dealId}/return-notice/acknowledge`, { method: "POST", body: { typed_name: name } });
            if (onDone) await onDone();
            else window.location.reload();
          } catch (e) { setError(e.message); }
        }}>I have returned or destroyed the materials</button>
      {error && <p className="mt-2 text-[#9B2335]" role="alert">{error}</p>}
    </div>
  );
}

function AgreementManager({ envelope, a, run }) {
  const v = envelope.vocabularies || {};
  const dealId = envelope.deal.id;
  const controls = agreementControls(envelope, a);
  const [pending, setPending] = useState({});
  const [policy, setPolicy] = useState("");
  const [scope, setScope] = useState("all");
  const [file, setFile] = useState(null);
  const kindLabel = (v.agreement_kinds || []).find((k) => k.key === a.kind)?.label || a.kind;
  const needsPolicy = (a.signatures || 0) > 0;
  const policySelect = needsPolicy && (
    <label className="mt-2 block text-xs text-text-secondary">
      {a.signatures} signature{a.signatures === 1 ? "" : "s"} on this agreement — existing signers:
      <select aria-label={`Re-signing for ${a.title}`} className="mt-1 block w-full rounded border border-border bg-white px-2 py-1 text-sm"
        value={policy} onChange={(e) => setPolicy(e.target.value)}>
        <option value="">Choose…</option>
        {(v.resign_policies || []).map((r) => <option key={r.key} value={r.key}>{r.label}</option>)}
      </select>
    </label>
  );
  return (
    <li className="rounded border border-border p-3" data-agreement={a.title}>
      <div className="flex items-center justify-between gap-2">
        <span className="font-medium text-navy">{a.sort_order}. {a.title}{" "}
          <span className="text-xs text-text-muted">
            {kindLabel}{a.current_version ? ` · v${a.current_version}` : ""} · {a.status}
            {a.is_master ? " · master NDA" : ""}{a.virtual ? " (from another deal)" : ""}
          </span>
        </span>
        {controls.includes("publish") && (
          <button type="button" className="text-xs font-medium text-navy hover:underline"
            onClick={() => run(() => call(`deals/${dealId}/agreements/${a.id}/publish`, { method: "POST" }))}>
            Publish
          </button>
        )}
      </div>
      {a.kind === "upload" && (
        <p className="mt-1 text-xs text-text-muted">Uploaded PDF — optional fill-in fields are not supported for uploads.</p>
      )}
      {a.kind !== "upload" && !a.virtual && (a.fields || []).map((f) => (
        <label key={f.key} className="mt-2 block text-xs text-text-secondary">
          {f.label}
          <input className="mt-1 block w-full rounded border border-border px-2 py-1 text-sm"
            defaultValue={a.field_values?.[f.key] ?? f.default ?? ""}
            onBlur={(e) => {
              const value = f.type === "flag" ? ["on", "true", "yes", "1"].includes(e.target.value.toLowerCase())
                : e.target.value;
              if (a.status === "draft") {
                run(() => call(`deals/${dealId}/agreements/${a.id}`, { method: "PATCH",
                  body: { field_values: { [f.key]: value } } }));
              } else {
                setPending((p) => ({ ...p, [f.key]: value }));
              }
            }} />
        </label>
      ))}
      {a.status === "draft" && (a.blockers || []).length > 0 && (
        <p className="mt-2 text-xs text-[#9B2335]">Missing before publishing: {a.blockers.map((b) => b.label).join(", ")}</p>
      )}
      {controls.includes("new_version") && (
        <div className="mt-2" data-testid="new-version">
          {policySelect}
          <button type="button" disabled={!Object.keys(pending).length || (needsPolicy && !policy)}
            className="mt-2 rounded border border-navy px-3 py-1 text-xs font-medium text-navy disabled:opacity-50"
            onClick={() => run(async () => {
              await call(`deals/${dealId}/agreements/${a.id}/versions`, { method: "POST",
                body: { field_values: pending, resign_policy: policy || null } });
              setPending({});
            })}>Save as version {(a.current_version || 0) + 1}</button>
        </div>
      )}
      {controls.includes("replace_file") && (
        <div className="mt-2" data-testid="replace-file">
          <input type="file" accept="application/pdf,.pdf" aria-label={`Replacement PDF for ${a.title}`}
            className="text-xs" onChange={(e) => setFile(e.target.files?.[0] || null)} />
          {policySelect}
          <button type="button" disabled={!file || (needsPolicy && !policy)}
            className="mt-2 rounded border border-navy px-3 py-1 text-xs font-medium text-navy disabled:opacity-50"
            onClick={() => run(async () => {
              const form = new FormData();
              form.append("file", file, file.name);
              if (policy) form.append("resign_policy", policy);
              await call(`deals/${dealId}/agreements/${a.id}/versions/file`, { method: "POST", form });
              setFile(null);
            })}>Replace PDF (version {(a.current_version || 0) + 1})</button>
        </div>
      )}
      {controls.includes("make_master") && envelope.publisher_entity_id && (
        <div className="mt-2 flex flex-wrap items-center gap-2 text-xs" data-testid="make-master">
          <select aria-label="Master NDA scope" className="rounded border border-border bg-white px-2 py-1"
            value={scope} onChange={(e) => setScope(e.target.value)}>
            {(v.master_scopes || []).map((m) => <option key={m.key} value={m.key}>{m.label}</option>)}
          </select>
          <button type="button" className="rounded border border-navy px-3 py-1 font-medium text-navy"
            onClick={() => run(() => call(`publishers/${envelope.publisher_entity_id}/master-nda`, { method: "PUT",
              body: { deal_agreement_id: a.id, scope, deal_ids: scope === "selected" ? [dealId] : [] } }))}>
            Use as master NDA
          </button>
        </div>
      )}
    </li>
  );
}

function UploadAgreement({ dealId, run }) {
  const [title, setTitle] = useState("");
  const [file, setFile] = useState(null);
  return (
    <div className="mt-3 flex flex-wrap items-end gap-2 text-xs text-text-secondary" data-testid="upload-agreement">
      <label>Your own agreement (PDF only)
        <input className="mt-1 block rounded border border-border px-2 py-1 text-sm" placeholder="Title" value={title}
          onChange={(e) => setTitle(e.target.value)} />
      </label>
      <input type="file" accept="application/pdf,.pdf" aria-label="Agreement PDF"
        onChange={(e) => setFile(e.target.files?.[0] || null)} />
      <button type="button" disabled={!file || title.trim().length < 2}
        className="rounded border border-navy px-3 py-1.5 font-medium text-navy disabled:opacity-50"
        onClick={() => run(async () => {
          const form = new FormData();
          form.append("file", file, file.name);
          form.append("title", title);
          await call(`deals/${dealId}/agreements/upload`, { method: "POST", form });
          setTitle(""); setFile(null);
        })}>Upload agreement</button>
      <span className="w-full text-text-muted">Uploaded agreements are signed exactly as uploaded — no fill-in fields.</span>
    </div>
  );
}

function ManagerPanel({ envelope, templates, onChange, setError }) {
  const v = envelope.vocabularies || {};
  const s = envelope.settings || {};
  const dealId = envelope.deal.id;
  const [form, setForm] = useState({
    eligibility_rule: s.eligibility_rule, approval_mode: s.approval_mode,
    sponsor_display: s.sponsor_display, code_name: s.code_name || "",
    downloads: s.downloads, watermark: s.watermark, access_period: s.access_period,
    access_until: s.access_until || "", access_days: s.access_days ?? "", expiry_notice_days: s.expiry_notice_days ?? 7,
    notify_publisher_on: s.notify_publisher_on || [], offering_type: s.offering_type,
  });
  const payload = () => ({
    ...form, access_until: form.access_period === "until_date" ? form.access_until || null : null,
    access_days: form.access_period === "days_after_approval" ? Number(form.access_days) || null : null,
    expiry_notice_days: Number(form.expiry_notice_days),
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
        <div className="mt-3 grid gap-3 sm:grid-cols-2 text-sm text-text-secondary" data-testid="settings-rows">
          {settingsRows(envelope).map((row) => (
            <label key={row.key} data-setting={row.key}>{row.label}{sel(row.key, row.options)}</label>
          ))}
          {form.sponsor_display === "code_name" && (
            <label>Code name
              <input className="mt-1 block w-full rounded border border-border px-2 py-1.5" value={form.code_name}
                onChange={(e) => setForm({ ...form, code_name: e.target.value })} />
            </label>
          )}
          {form.access_period === "until_date" && (
            <label>Access ends on
              <input type="date" className="mt-1 block w-full rounded border border-border px-2 py-1.5"
                value={form.access_until} onChange={(e) => setForm({ ...form, access_until: e.target.value })} />
            </label>
          )}
          {form.access_period === "days_after_approval" && (
            <label>Days after approval
              <input type="number" min={1} className="mt-1 block w-full rounded border border-border px-2 py-1.5"
                value={form.access_days} onChange={(e) => setForm({ ...form, access_days: e.target.value })} />
            </label>
          )}
          {form.access_period !== "none" && (
            <label>Warn the investor (days before the end)
              <input type="number" min={0} max={90} className="mt-1 block w-full rounded border border-border px-2 py-1.5"
                value={form.expiry_notice_days} onChange={(e) => setForm({ ...form, expiry_notice_days: e.target.value })} />
            </label>
          )}
          <fieldset className="sm:col-span-2" data-setting="notify_publisher_on">
            <legend>Notify the publisher on</legend>
            <div className="mt-1 flex flex-wrap gap-3">
              {(v.notify_events || []).map((ev) => (
                <label key={ev.key} className="flex items-center gap-1">
                  <input type="checkbox" checked={form.notify_publisher_on.includes(ev.key)} onChange={(e) => setForm({
                    ...form, notify_publisher_on: e.target.checked ? [...form.notify_publisher_on, ev.key]
                      : form.notify_publisher_on.filter((k) => k !== ev.key) })} />
                  {ev.label}
                </label>
              ))}
            </div>
          </fieldset>
          {["rule_506b", "rule_506c"].includes(form.offering_type) && envelope.notices?.offering_draft && (
            <p className="sm:col-span-2 text-xs text-[#9B2335]" data-testid="offering-draft">{envelope.notices.offering_draft}</p>
          )}
          {form.downloads === "view_only" && (
            <p className="sm:col-span-2 text-xs text-text-muted">View-only deters copying but cannot stop screenshots.</p>
          )}
        </div>
        <button type="button" className="mt-3 rounded bg-navy px-4 py-2 text-sm font-medium text-white"
          onClick={() => run(() => call(`deals/${dealId}/settings`, { method: "PUT", body: payload() }))}>Save settings</button>
      </section>

      <section className={`${CARD} p-5`}>
        <h3 className="text-base font-semibold text-navy">Agreements</h3>
        {envelope.master_nda && (
          <p className="mt-2 text-xs text-text-muted" data-testid="master-nda-status">
            Master NDA set for this publisher ({(v.master_scopes || []).find((m) => m.key === envelope.master_nda.scope)?.label
              || envelope.master_nda.scope}){envelope.master_nda.covers_this_deal ? " — it covers this deal." : "."}
          </p>
        )}
        <ol className="mt-3 space-y-3 text-sm">
          {(envelope.agreements || []).map((a) => (
            <AgreementManager key={a.id} envelope={envelope} a={a} run={run} />
          ))}
        </ol>
        <UploadAgreement dealId={dealId} run={run} />
        <div className="mt-3 flex flex-wrap gap-2">
          {(templates || []).map((t) => (
            <button key={t.id} type="button" className="rounded border border-navy px-3 py-1.5 text-xs font-medium text-navy"
              onClick={() => run(() => call(`deals/${dealId}/agreements`, { method: "POST", body: { template_id: t.id } }))}>
              Require {t.title} (v{t.version}{t.scope === "org" ? ", firm library" : ""}{t.status === "draft" ? ", DRAFT" : ""})
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

function QaLoader({ envelope }) {
  const [qa, setQa] = useState(null);
  const [error, setError] = useState(null);
  if (qa) return <QAPanel dealId={envelope.deal.id} initial={qa} folders={folderRows(envelope)} />;
  return (
    <section className={`${CARD} p-5`}>
      <h3 className="text-base font-semibold text-navy">Questions and answers</h3>
      <button type="button" className="mt-2 text-sm text-navy hover:underline" onClick={async () => {
        try { setQa(await call(`deals/${envelope.deal.id}/questions`)); } catch (e) { setError(e.message); }
      }}>Open Q&amp;A</button>
      {error && <p className="mt-2 text-sm text-[#9B2335]" role="alert">{error}</p>}
    </section>
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
  const notices = roomNotices(envelope);
  const step = investorStep(envelope).step;
  const showQa = manage || step === "granted" || step === "advisor";
  return (
    <div className="space-y-6">
      {!manage && notices.length > 0 && (
        <div className={`${CARD} p-4 text-sm text-text-secondary`} data-testid="room-notices">
          {notices.map((n) => <p key={n}>{n}</p>)}
        </div>
      )}
      {!manage && envelope.advisor && <AdvisorAcknowledgement envelope={envelope} onChange={refresh} />}
      {!manage && !envelope.advisor && <InvestorPanel envelope={envelope} onChange={refresh} />}
      <section className={`${CARD} p-5`}>
        <h3 className="text-base font-semibold text-navy">Data room</h3>
        <FolderList envelope={envelope} manage={manage} levels={envelope.vocabularies?.levels || []}
          onChange={refresh} setError={setError} />
      </section>
      {showQa && <QaLoader envelope={envelope} />}
      {!manage && !envelope.advisor && envelope.advisors && <InvestorAdvisors envelope={envelope} onChange={refresh} />}
      {manage && <ManagerPanel envelope={envelope} templates={templates} onChange={refresh} setError={setError} />}
      {manage && <ActivityPanel envelope={envelope} />}
      {error && <p className="text-sm text-[#9B2335]" role="alert">{error}</p>}
    </div>
  );
}
