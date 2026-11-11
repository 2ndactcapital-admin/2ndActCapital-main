"use client";

import { useEffect, useState } from "react";
import {
  IMPORT_EXPORT_NOTE, SIMULATED_NOTE, bookKinds, canAccept, canWrite, conformanceSummary, consentOffered, deliveries,
  exportHref, groupsOf, labelOf, lastFourRef, liveKey, rejectionRows, shortDate, tabsFor, templateHref, transferRow,
  typeOf, vocab,
} from "@/lib/counterparties.mjs";
import { refreshExpiryText } from "@/lib/custodianFeed.mjs";

// CPSIM01 — Admin → Counterparties (Super Admin): the registry of SIMULATED counterparties. Every screen carries the
// SIMULATED banner; every write control renders only inside canWrite(envelope) (exactly true — fails closed); labels,
// types, field groups, statuses, outcomes and tabs are the server's.
const card = "rounded-[6px] border border-[#ece8dd] bg-white p-5";
const btn = "rounded border border-[#C5A880] px-3 py-1 text-sm text-navy hover:bg-[#F5F1EB] disabled:opacity-50";
const input = "mt-1 w-full rounded border border-[#E2E8F0] bg-white px-2 py-1 text-sm";
const API = "/api/admin/counterparties";

async function call(path, method = "GET", body) {
  const res = await fetch(`${API}${path ? "/" + path : ""}`, {
    method, headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data?.error || `Request failed (${res.status})`);
  return data;
}

async function upload(path, form) {
  const res = await fetch(`${API}/${path}`, { method: "POST", body: form });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data?.error || `Request failed (${res.status})`);
  return data;
}

// Loads a tab's own envelope once (unless the page already supplied it).
function useLoaded(initial, path) {
  const [data, setData] = useState(initial || null);
  const [error, setError] = useState(null);
  useEffect(() => {
    if (initial || !path) return;
    call(path).then(setData).catch((e) => setError(e.message));
  }, [initial, path]);
  return [data, setData, error];
}

export function SimulatedBanner() {
  return (
    <div role="note" data-testid="simulated-banner"
      className="rounded-[6px] border border-[#C5A880] bg-[#F5F1EB] px-4 py-3 text-sm text-navy">
      <span className="font-semibold tracking-[0.12em]">SIMULATED</span>
      <span className="ml-2 text-[#334155]">{SIMULATED_NOTE}</span>
    </div>
  );
}

export function IssuedSecrets({ issued }) {
  const entries = Object.entries(issued || {});
  if (!entries.length) return null;
  return (
    <div className="mt-3 rounded-[6px] border border-[#C5A880] bg-[#FAF9F6] p-4 text-sm" data-testid="issued-secrets">
      <p className="font-semibold text-navy">Shown once — copy it now. It is stored only as a hash and can never be
        shown again.</p>
      {entries.map(([k, v]) => (
        <p key={k} className="mt-2"><span className="text-[#64748B]">{k === "api_key" ? "Key" : "FIX password"}: </span>
          <code className="break-all">{v}</code></p>
      ))}
    </div>
  );
}

export function CounterpartyList({ envelope }) {
  const rows = envelope?.rows || [];
  const statuses = vocab(envelope, "statuses");
  if (!rows.length) {
    return <div className={`${card} text-sm text-[#64748B]`} data-testid="cp-empty">No simulated counterparties yet.</div>;
  }
  return (
    <div className={card} data-testid="cp-list">
      <table className="w-full text-left text-sm">
        <thead className="text-[#64748B]"><tr><th>Name</th><th>Institution type</th><th>Status</th><th>Key</th>
          <th>Business date</th><th>Last request</th></tr></thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id} className="border-t border-[#ece8dd]">
              <td className="py-2"><a className="text-navy underline" href={`/admin/counterparties/${r.id}`}>{r.name}</a>
                <div className="text-xs text-[#64748B]">{r.slug}</div></td>
              <td>{r.institution_type_label}</td>
              <td>{labelOf(statuses, r.status)}</td>
              <td>{r.key_last4 ? `…${r.key_last4}` : "none"}</td>
              <td>{r.business_date || ""}</td>
              <td>{shortDate(r.last_request_at)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function CreateWizard({ envelope, initialType = "", initialResult = null }) {
  const types = vocab(envelope, "institution_types");
  const groupLabels = vocab(envelope, "field_groups");
  const [name, setName] = useState("");
  const [type, setType] = useState(initialType);
  const [result, setResult] = useState(initialResult);
  const [error, setError] = useState(null);
  if (!canWrite(envelope)) return null;
  const groups = groupsOf(envelope, type);
  async function submit(e) {
    e.preventDefault();
    setError(null);
    try {
      setResult(await call("", "POST", { name, institution_type: type }));
    } catch (err) {
      setError(err.message);
    }
  }
  return (
    <section className={card} data-testid="cp-wizard">
      <h2 className="font-serif text-xl text-navy">New simulated counterparty</h2>
      {result ? (
        <div>
          <p className="mt-2 text-sm">Created <a className="underline" href={`/admin/counterparties/${result.id}`}>
            {result.slug}</a> as a draft. Activate it from its page once the key is copied.</p>
          <IssuedSecrets issued={result.issued} />
          <SendTestMessage counterpartyId={result.id} envelope={envelope} initialKey={result.issued?.api_key || ""} />
        </div>
      ) : (
        <form onSubmit={submit} className="mt-3 grid gap-3 md:grid-cols-2">
          <label className="text-sm">Name<input className={input} value={name} onChange={(e) => setName(e.target.value)}
            required maxLength={200} /></label>
          <label className="text-sm">Institution type
            <select className={input} value={type} onChange={(e) => setType(e.target.value)} required>
              <option value="">Choose…</option>
              {types.map((t) => <option key={t.key} value={t.key}>{t.label}</option>)}
            </select></label>
          {type ? (
            <div className="md:col-span-2 text-sm" data-testid="cp-wizard-groups">
              <p className="text-[#64748B]">{typeOf(envelope, type)?.description}</p>
              <p className="mt-1">Sections for this type: {groups.map((g) => labelOf(groupLabels, g)).join(" · ")}</p>
              <p className="mt-1 text-[#64748B]">A key{groups.includes("fix") ? ", a FIX password" : ""}
                {groups.some((g) => g.startsWith("bank_")) ? " and fake bank details" : ""} are generated on create.</p>
            </div>
          ) : null}
          {error ? <p className="text-sm text-[#9B2335] md:col-span-2">{error}</p> : null}
          <div className="md:col-span-2"><button type="submit" className={btn} disabled={!name || !type}>Create</button></div>
        </form>
      )}
    </section>
  );
}

export function SendTestMessage({ counterpartyId, envelope, initialKey = "", initialReply = null }) {
  const [key, setKey] = useState(initialKey);
  const [reply, setReply] = useState(initialReply);
  const [error, setError] = useState(null);
  if (!canWrite(envelope)) return null;
  async function send() {
    setError(null);
    try {
      setReply(await call(`${counterpartyId}/test-message`, "POST", { key }));
    } catch (err) {
      setError(err.message);
    }
  }
  return (
    <div className="mt-4" data-testid="cp-test-message">
      <h3 className="font-serif text-lg text-navy">Send test message</h3>
      <p className="text-xs text-[#64748B]">Posts a sample envelope to this counterparty's mirror with the key you paste
        (it is not stored).</p>
      <div className="mt-2 flex gap-2">
        <input className={input} type="password" placeholder="cpsim_live_…" value={key}
          onChange={(e) => setKey(e.target.value)} />
        <button type="button" className={btn} onClick={send} disabled={!key}>Send test message</button>
      </div>
      {error ? <p className="mt-2 text-sm text-[#9B2335]">{error}</p> : null}
      {reply ? (
        <div className="mt-2 text-sm" data-testid="cp-test-reply">
          <p>Reply: HTTP {reply.status}{reply.simulated_header === "true" ? " · SIMULATED" : ""}</p>
          <pre className="mt-1 overflow-x-auto rounded bg-[#FAF9F6] p-2 text-xs">{JSON.stringify(reply.reply, null, 2)}</pre>
        </div>
      ) : null}
    </div>
  );
}

function Field({ label, value }) {
  return <div><dt className="text-[#64748B]">{label}</dt><dd className="text-[#0F172A]">{value ?? ""}</dd></div>;
}

function ConnectionTab({ env, d, act }) {
  const key = liveKey(d);
  const w = canWrite(env);
  const status = d.counterparty?.status;
  return (
    <div data-testid="tab-connection">
      <dl className="grid gap-3 text-sm md:grid-cols-2">
        <Field label="Destination URL" value={<span><code>{env.destination_url}</code>{" "}
          <button type="button" className="text-xs underline" onClick={() => navigator.clipboard?.writeText(env.destination_url)}>
            Copy</button></span>} />
        <Field label="Key" value={key ? `cpsim_live_…${key.last4}` : "none"} />
        <Field label="Status" value={labelOf(vocab(env, "statuses"), status)} />
        <Field label="Slug" value={d.counterparty?.slug} />
      </dl>
      {w ? (
        <div className="mt-4 flex flex-wrap gap-2" data-testid="connection-actions">
          {key ? <button type="button" className={btn} onClick={() => act("key/rotate")}>Rotate key</button>
            : <button type="button" className={btn} onClick={() => act("key/generate")}>Generate key</button>}
          {key ? <button type="button" className={btn} onClick={() => act("key/revoke")}>Revoke key</button> : null}
          {status !== "active" ? <button type="button" className={btn} onClick={() => act("activate")}>Activate</button>
            : <button type="button" className={btn} onClick={() => act("disable")}>Disable</button>}
        </div>
      ) : null}
      <SendTestMessage counterpartyId={d.counterparty?.id} envelope={env} />
    </div>
  );
}

function FixTab({ env, d, act }) {
  const f = d.fix_session;
  if (!f) return <p className="text-sm text-[#64748B]" data-testid="tab-fix">No FIX session.</p>;
  return (
    <div data-testid="tab-fix">
      <p className="text-xs text-[#64748B]">Stored only — there is no FIX engine in this version.</p>
      <dl className="mt-2 grid gap-3 text-sm md:grid-cols-3">
        <Field label="BeginString" value={f.begin_string} /><Field label="SenderCompID" value={f.sender_comp_id} />
        <Field label="TargetCompID" value={f.target_comp_id} /><Field label="Host" value={f.host} />
        <Field label="Port" value={String(f.port)} /><Field label="HeartBtInt" value={String(f.heartbt_int)} />
        <Field label="ResetOnLogon" value={f.reset_on_logon ? "Yes" : "No"} /><Field label="Username" value={f.username || ""} />
        <Field label="Password" value={f.password_last4 ? `cpsim_fixpw_…${f.password_last4}` : "none"} />
      </dl>
      {canWrite(env) ? (
        <div className="mt-3 flex gap-2" data-testid="fix-actions">
          <select className="rounded border border-[#E2E8F0] px-2 py-1 text-sm" defaultValue={f.begin_string}
            onChange={(e) => act("fix", "PUT", { begin_string: e.target.value })}>
            {vocab(env, "begin_strings").map((b) => <option key={b} value={b}>{b}</option>)}
          </select>
          <button type="button" className={btn} onClick={() => act("fix", "PUT", { rotate_password: true })}>
            Rotate FIX password</button>
        </div>
      ) : null}
    </div>
  );
}

function BankTab({ env, d, act }) {
  return (
    <div data-testid="tab-bank">
      <p className="text-xs text-[#64748B]">Fake by construction — held only in the simulator, never typed from a real
        account.</p>
      {(d.bank_details || []).map((b) => (
        <dl key={b.id} className="mt-3 grid gap-3 text-sm md:grid-cols-3">
          <Field label="Bank" value={b.bank_name} />
          {b.aba_wire ? <Field label="ABA (wire)" value={b.aba_wire} /> : null}
          {b.aba_ach ? <Field label="ABA (ACH)" value={b.aba_ach} /> : null}
          {b.account_number ? <Field label="Account" value={b.account_number} /> : null}
          {b.bic ? <Field label="SWIFT / BIC" value={b.bic} /> : null}
          {b.iban ? <Field label="IBAN" value={b.iban} /> : null}
          {b.intermediary_bank_name ? <Field label="Intermediary bank" value={b.intermediary_bank_name} /> : null}
          {b.intermediary_bic ? <Field label="Intermediary BIC" value={b.intermediary_bic} /> : null}
          <Field label="Beneficiary" value={b.beneficiary_name} />
          <Field label="Beneficiary address" value={b.beneficiary_address} />
        </dl>
      ))}
      {canWrite(env) ? <button type="button" className={`${btn} mt-3`} onClick={() => act("bank/regenerate")}>
        Regenerate bank details</button> : null}
    </div>
  );
}

function BehaviourTab({ env, d, act }) {
  const b = d.behaviour || {};
  const outcomes = vocab(env, "outcomes");
  return (
    <div data-testid="tab-behaviour">
      <p className="text-xs text-[#64748B]">Stored now; drives outcomes from cpsim03 (orders) and cpsim04 (payments).</p>
      <dl className="mt-2 grid gap-3 text-sm md:grid-cols-3">
        <Field label="Default outcome" value={labelOf(outcomes, b.default_outcome) || b.default_outcome} />
        {b.reject_reason ? <Field label="Reject reason" value={b.reject_reason} /> : null}
        {b.partial_pct != null ? <Field label="Partial %" value={String(b.partial_pct)} /> : null}
        {b.delay_seconds != null ? <Field label="Delay (seconds)" value={String(b.delay_seconds)} /> : null}
        <Field label="ACH return" value={b.ach_return_code ? `${b.ach_return_code} after ${b.ach_return_after_days} business days` : "none"} />
        <Field label="Webhooks" value={b.webhooks_enabled ? "On" : "Off"} />
        <Field label="Member consent (transfers)" value={b.consent_required === true ? "Required" : "Not required"} />
      </dl>
      {canWrite(env) && outcomes.length ? (
        <div className="mt-3 flex flex-wrap gap-2" data-testid="behaviour-actions">
          {outcomes.filter((o) => o.key === "accept").map((o) => (
            <button key={o.key} type="button" className={btn}
              onClick={() => act("behaviour", "PUT", { default_outcome: o.key })}>{o.label}</button>))}
          <button type="button" className={btn}
            onClick={() => act("behaviour", "PUT", { webhooks_enabled: !b.webhooks_enabled })}>
            {b.webhooks_enabled ? "Webhooks off" : "Webhooks on"}</button>
          <button type="button" className={btn}
            onClick={() => act("behaviour", "PUT", { consent_required: !(b.consent_required === true) })}>
            {b.consent_required === true ? "Consent not required" : "Require member consent"}</button>
        </div>
      ) : null}
    </div>
  );
}

export function ClockTab({ env, d, act, advance = null }) {
  const clock = d.clock || [];
  const filled = Array.isArray(advance?.filled) ? advance.filled : null;
  return (
    <div data-testid="tab-clock">
      <p className="text-sm">Business date: <span className="font-semibold text-navy">{clock[0]?.business_date || ""}</span></p>
      {canWrite(env) ? <button type="button" className={`${btn} mt-2`} onClick={() => act("clock/advance")}>
        Advance one business day</button> : null}
      {filled ? (
        <div className="mt-2 text-sm" data-testid="advance-filled">
          <p>Advanced {advance.from} → {advance.to}. {filled.length ? `Working orders re-evaluated: ${filled.length}.` : "No working order filled."}</p>
          {Array.isArray(advance?.transfers) && advance.transfers.length ? (
            <ul className="mt-1 text-xs" data-testid="advance-transfers">{advance.transfers.map((t) => (
              <li key={t.transfer_id}>{t.transfer_id} — {t.from} → {t.status}{t.reject_reason ? ` (${t.reject_reason})` : ""}{t.return_code ? ` (${t.return_code})` : ""} · {t.amount} {t.rail}</li>))}</ul>
          ) : null}
          {Array.isArray(advance?.fills_cash) && advance.fills_cash.length ? (
            <p className="text-xs" data-testid="advance-fills-cash">Fills settled in cash: {advance.fills_cash.length}.</p>) : null}
          {filled.length ? <ul className="mt-1 text-xs">{filled.map((f) => (
            <li key={f.order_id}>{f.order_id} — {f.status}{f.reject_reason ? ` (${f.reject_reason})` : ""}{f.executions?.length ? ` · ${f.executions.length} execution(s) at ${f.price}` : ""}</li>))}</ul> : null}
        </div>
      ) : null}
      <ul className="mt-3 text-xs text-[#64748B]">
        {clock.map((c, i) => <li key={i}>{c.business_date} — {c.reason}</li>)}
      </ul>
    </div>
  );
}

function ActivityTab({ requests }) {
  const rows = requests?.rows || [];
  return (
    <div data-testid="tab-activity">
      {rows.length ? (
        <table className="w-full text-left text-xs">
          <thead className="text-[#64748B]"><tr><th>At</th><th>Method</th><th>Path</th><th>Status</th><th>Auth</th>
            <th>Shape</th><th>Message type</th></tr></thead>
          <tbody>{rows.map((r) => (
            <tr key={r.id} className="border-t border-[#ece8dd]"><td>{r.at}</td><td>{r.method}</td><td>{r.path}</td>
              <td>{r.status}</td><td>{r.auth_outcome}</td><td>{r.shape_valid == null ? "" : r.shape_valid ? "valid" : "invalid"}</td>
              <td>{r.message_type || ""}</td></tr>))}</tbody>
        </table>
      ) : <p className="text-sm text-[#64748B]">No requests yet.</p>}
    </div>
  );
}

// ── cpsim02: Import / Export ───────────────────────────────────────────────────────────────────────────────────
export function RejectionTable({ preview, env }) {
  const rows = rejectionRows(preview, env);
  if (!rows.length) return <p className="mt-2 text-sm text-[#2D6A4F]" data-testid="no-rejections">No rejected rows.</p>;
  return (
    <table className="mt-2 w-full text-left text-xs" data-testid="rejection-table">
      <thead className="text-[#64748B]"><tr><th>Row</th><th>Id</th><th>Reason</th><th>Detail</th></tr></thead>
      <tbody>{rows.map((r) => (
        <tr key={`${r.row}-${r.reason}`} className="border-t border-[#ece8dd]" data-reason={r.reason}>
          <td>{r.row}</td><td>{r.id}</td><td className="text-[#9B2335]">{r.label}</td><td>{r.detail}</td></tr>))}</tbody>
    </table>
  );
}

export function ImportExportTab({ id, initialBook, initialPreview = null }) {
  const [book, setBook, loadError] = useLoaded(initialBook, initialBook ? null : `${id}/book`);
  const [kind, setKind] = useState("positions");
  const [file, setFile] = useState(null);
  const [preview, setPreview] = useState(initialPreview);
  const [accepted, setAccepted] = useState(null);
  const [error, setError] = useState(null);
  const env = book;
  const kinds = bookKinds(env);
  const w = canWrite(env);
  function form() {
    const f = new FormData();
    f.append("kind", kind);
    f.append("file", file);
    return f;
  }
  async function doPreview() {
    setError(null); setAccepted(null);
    try { setPreview(await upload(`${id}/book/preview`, form())); } catch (e) { setError(e.message); }
  }
  async function doAccept() {
    setError(null);
    try {
      setAccepted(await upload(`${id}/book/accept`, form()));
      setPreview(null);
      setBook(await call(`${id}/book`));
    } catch (e) { setError(e.message); }
  }
  if (loadError) return <p className="text-sm text-[#9B2335]" data-testid="tab-import-export">{loadError}</p>;
  if (!env) return <p className="text-sm text-[#64748B]" data-testid="tab-import-export">Loading the book…</p>;
  return (
    <div data-testid="tab-import-export" className="space-y-4">
      <p className="text-xs text-[#64748B]">The simulated custodian&apos;s book. Each upload REPLACES the rows of the
        accounts it names; positions and transactions are validated against the security master. Served back through
        the simulator&apos;s API on its clock date.</p>
      <dl className="grid gap-3 text-sm md:grid-cols-4" data-testid="book-counts">
        {kinds.map((k) => <Field key={k.key} label={k.label} value={String(env.counts?.[k.key] ?? 0)} />)}
      </dl>
      <div>
        <p className="text-sm font-semibold text-navy">Templates</p>
        <ul className="mt-1 flex flex-wrap gap-3 text-sm" data-testid="book-templates">
          {kinds.map((k) => (
            <li key={k.key}>{k.label}: <a className="text-navy underline" href={templateHref(id, k.key, "csv")}>CSV</a>
              {" · "}<a className="text-navy underline" href={templateHref(id, k.key, "json")}>JSON</a></li>))}
        </ul>
      </div>
      {w ? (
        <div className="rounded-[6px] border border-[#ece8dd] p-3" data-testid="book-upload">
          <p className="text-sm font-semibold text-navy">Upload</p>
          <div className="mt-2 flex flex-wrap items-center gap-2 text-sm">
            <select className="rounded border border-[#E2E8F0] px-2 py-1" value={kind} onChange={(e) => setKind(e.target.value)}>
              {kinds.map((k) => <option key={k.key} value={k.key}>{k.label}</option>)}
            </select>
            <input type="file" accept=".csv,.json" onChange={(e) => { setFile(e.target.files?.[0] || null); setPreview(null); }} />
            <button type="button" className={btn} disabled={!file} onClick={doPreview}>Preview</button>
            {canAccept(env, preview) && file ? <button type="button" className={btn} onClick={doAccept}
              data-testid="book-accept">Accept {preview.counts.accepted} row(s)</button> : null}
          </div>
        </div>
      ) : null}
      {error ? <p className="text-sm text-[#9B2335]">{error}</p> : null}
      {preview ? (
        <div data-testid="book-preview">
          <p className="text-sm">Preview of {preview.file_name}: <span className="text-[#2D6A4F]">{preview.counts?.accepted} accepted</span>,{" "}
            <span className="text-[#9B2335]">{preview.counts?.rejected} rejected</span> of {preview.counts?.rows}.</p>
          <RejectionTable preview={preview} env={env} />
        </div>
      ) : null}
      {accepted ? <p className="text-sm text-[#2D6A4F]" data-testid="book-accepted">Accepted {accepted.counts?.accepted} row(s);
        {" "}{(accepted.webhooks || []).length} webhook event(s).</p> : null}
      <div>
        <p className="text-sm font-semibold text-navy">Export</p>
        <ul className="mt-1 flex flex-wrap gap-3 text-sm" data-testid="book-export">
          {kinds.map((k) => <li key={k.key}><a className="text-navy underline" href={exportHref(id, "csv", k.key)}>{k.label} (CSV)</a></li>)}
          <li><a className="text-navy underline" href={exportHref(id, "json")}>Whole book as the API serves it (JSON)</a></li>
        </ul>
      </div>
      <div>
        <p className="text-sm font-semibold text-navy">Upload history</p>
        {(env.uploads || []).length ? (
          <table className="mt-1 w-full text-left text-xs" data-testid="book-history">
            <thead className="text-[#64748B]"><tr><th>At</th><th>File</th><th>Kind</th><th>Accepted</th><th>Rejected</th></tr></thead>
            <tbody>{env.uploads.map((u) => (
              <tr key={u.id} className="border-t border-[#ece8dd]"><td>{shortDate(u.uploaded_at)}</td><td>{u.file_name}</td>
                <td>{labelOf(kinds, u.kind)}</td><td>{u.rows_accepted}</td><td>{u.rows_rejected}</td></tr>))}</tbody>
          </table>
        ) : <p className="text-sm text-[#64748B]">No uploads yet.</p>}
      </div>
    </div>
  );
}

// ── cpsim02: Connections — allowed orgs, point / connect / sync (through the /altruist functions) ─────────────────
export function ConnectionsTab({ id, initialConnections }) {
  const [env, setEnv, loadError] = useLoaded(initialConnections, initialConnections ? null : `${id}/connections`);
  const [result, setResult] = useState(null);
  const [error, setError] = useState(null);
  if (loadError) return <p className="text-sm text-[#9B2335]" data-testid="tab-connections">{loadError}</p>;
  if (!env) return <p className="text-sm text-[#64748B]" data-testid="tab-connections">Loading…</p>;
  const w = canWrite(env);
  async function act(path, method = "POST", body) {
    setError(null);
    try { setResult(await call(`${id}/connections/${path}`, method, body)); setEnv(await call(`${id}/connections`)); }
    catch (e) { setError(e.message); }
  }
  const rows = env.rows || [];
  return (
    <div data-testid="tab-connections">
      <p className="text-xs text-[#64748B]">Organizations with counterparty_sim.allowed on. Point one at this counterparty,
        then Connect (server-to-server authorize, the same callback handling) and Sync — the existing Altruist-compatible
        integration does the work; nothing here writes a portfolio table.</p>
      {error ? <p className="text-sm text-[#9B2335]">{error}</p> : null}
      {rows.length ? (
        <table className="mt-2 w-full text-left text-sm" data-testid="connections-table">
          <thead className="text-[#64748B]"><tr><th>Organization</th><th>Test</th><th>Points here</th><th>Connection</th>
            <th>Refresh token</th><th>Last sync</th><th /></tr></thead>
          <tbody>{rows.map((o) => (
            <tr key={o.id} className="border-t border-[#ece8dd]">
              <td className="py-1">{o.name}</td><td>{o.is_test ? "Test" : "Real (demo households only)"}</td>
              <td>{o.points_here ? "Yes" : o.counterparty_slug ? `No (${o.counterparty_slug})` : "No"}</td>
              <td>{o.connection_status || "none"}</td>
              {(() => { const x = refreshExpiryText(o); return (
                <td data-testid={x?.expired ? "refresh-expired" : "refresh-expiry"} className={x?.expired ? "text-[#9B2335]" : ""}>
                  {x ? x.text : ""}</td>); })()}
              <td>{o.last_sync ? `${o.last_sync.accounts_synced ?? 0} account(s), ${o.last_sync.positions_created ?? 0} position(s)` : ""}</td>
              <td>{w ? (
                <span className="flex flex-wrap gap-1">
                  <button type="button" className={btn} onClick={() => act(o.id, "PUT", { point: !o.points_here })}>
                    {o.points_here ? "Unpoint" : "Point here"}</button>
                  {o.points_here ? <button type="button" className={btn} onClick={() => act(`${o.id}/connect`)}>Connect</button> : null}
                  {o.points_here && o.connection_status ? <button type="button" className={btn}
                    onClick={() => act(`${o.id}/sync`)}>Sync now</button> : null}
                </span>) : null}</td>
            </tr>))}</tbody>
        </table>
      ) : <p className="mt-2 text-sm text-[#64748B]" data-testid="connections-empty">No organization is allowed yet (Org access).</p>}
      {result?.sync ? <p className="mt-2 text-sm text-[#2D6A4F]" data-testid="connections-result">Synced {result.sync.accounts_synced} account(s):
        {" "}{result.sync.positions_created} position(s), {result.sync.transactions_created} transaction(s) created.</p> : null}
    </div>
  );
}

// ── cpsim02: Webhooks — the flag + override URL (Behaviour row), "Send test event", the delivery log ──────────────
export function WebhooksTab({ env, d, act, requests }) {
  const b = d.behaviour || {};
  const [account, setAccount] = useState("");
  const [url, setUrl] = useState(b.webhook_url || "");
  const [sent, setSent] = useState(null);
  const [error, setError] = useState(null);
  const w = canWrite(env);
  async function test() {
    setError(null);
    try { setSent((await call(`${d.counterparty.id}/webhooks/test`, "POST", { account_id: account })).delivery); }
    catch (e) { setError(e.message); }
  }
  const out = deliveries(requests);
  return (
    <div data-testid="tab-webhooks" className="space-y-3">
      <dl className="grid gap-3 text-sm md:grid-cols-3">
        <Field label="Webhooks" value={b.webhooks_enabled ? "On" : "Off"} />
        <Field label="Target" value={b.webhook_url || "CPSIM_BASE_URL + /api/v1/altruist/webhook"} />
        <Field label="Signature" value="X-Altruist-Signature (HMAC-SHA256, simulator secret)" />
      </dl>
      {w ? (
        <div className="flex flex-wrap items-center gap-2 text-sm" data-testid="webhook-actions">
          <button type="button" className={btn} onClick={() => act("behaviour", "PUT", { webhooks_enabled: !b.webhooks_enabled })}>
            {b.webhooks_enabled ? "Webhooks off" : "Webhooks on"}</button>
          <input className="rounded border border-[#E2E8F0] px-2 py-1" placeholder="https:// override (optional)" value={url}
            onChange={(e) => setUrl(e.target.value)} />
          <button type="button" className={btn} onClick={() => act("behaviour", "PUT", { webhook_url: url || null })}>Save URL</button>
          <input className="rounded border border-[#E2E8F0] px-2 py-1" placeholder="account id in the book" value={account}
            onChange={(e) => setAccount(e.target.value)} />
          <button type="button" className={btn} disabled={!account || !b.webhooks_enabled} onClick={test}>Send test event</button>
        </div>
      ) : null}
      {error ? <p className="text-sm text-[#9B2335]">{error}</p> : null}
      {sent ? <p className="text-sm" data-testid="webhook-sent">{sent.event_type} → {sent.outcome} (HTTP {sent.status ?? "—"},
        {" "}{sent.attempts} attempt(s))</p> : null}
      <p className="text-sm font-semibold text-navy">Deliveries</p>
      {out.length ? (
        <table className="w-full text-left text-xs" data-testid="webhook-deliveries">
          <thead className="text-[#64748B]"><tr><th>At</th><th>Event</th><th>Status</th><th>Outcome</th><th>Latency</th></tr></thead>
          <tbody>{out.map((r) => (
            <tr key={r.id} className="border-t border-[#ece8dd]"><td>{r.at}</td><td>{r.message_type}</td><td>{r.status}</td>
              <td>{r.auth_outcome}</td><td>{r.latency_ms == null ? "" : `${r.latency_ms} ms`}</td></tr>))}</tbody>
        </table>
      ) : <p className="text-sm text-[#64748B]">No deliveries yet.</p>}
    </div>
  );
}

// ── cpsim03: Orders — what this simulated custodian received, its outcome and executions (read-only) ──────────────
export function OrdersTab({ id, initialOrders }) {
  const [env, , loadError] = useLoaded(initialOrders, initialOrders ? null : `${id}/orders`);
  if (loadError) return <p className="text-sm text-[#9B2335]" data-testid="tab-orders">{loadError}</p>;
  if (!env) return <p className="text-sm text-[#64748B]" data-testid="tab-orders">Loading…</p>;
  const rows = Array.isArray(env.rows) ? env.rows : [];
  const statuses = Array.isArray(env.status_labels) ? env.status_labels : [];
  const outcomes = Array.isArray(env.outcome_labels) ? env.outcome_labels : [];
  const limits = Array.isArray(env.v1_limits) ? env.v1_limits : [];
  return (
    <div data-testid="tab-orders" className="space-y-3">
      <p className="text-xs text-[#64748B]">Orders sent by Hollisworks to this SIMULATED custodian — read-only. A person
        sent each one after approval; the outcome is the behaviour in force when it arrived.</p>
      {limits.length ? <ul className="text-xs text-[#64748B]" data-testid="orders-v1-limits">{limits.map((l) => <li key={l}>{l}</li>)}</ul> : null}
      {rows.length ? (
        <table className="w-full text-left text-xs">
          <thead className="text-[#64748B]"><tr><th>Order</th><th>Account</th><th>Side</th><th>Amount</th><th>Type</th>
            <th>Outcome</th><th>Status</th><th>Executions</th><th>Received</th></tr></thead>
          <tbody>{rows.map((o) => (
            <tr key={o.order_id} className="border-t border-[#ece8dd] align-top" data-order={o.order_id}>
              <td>{o.order_id}<span className="ml-1 rounded border border-[#C5A880] px-1 text-[10px] font-semibold">SIMULATED</span></td>
              <td>{o.account_id}</td><td>{o.side}</td><td>{o.quantity ?? `${o.notional} notional`}</td>
              <td>{o.order_type}{o.limit_price ? ` ${o.limit_price}` : ""}</td><td>{labelOf(outcomes, o.outcome)}</td>
              <td>{labelOf(statuses, o.status)}{o.reject_reason ? ` — ${o.reject_reason}` : ""}</td>
              <td>{(o.executions || []).map((e) => <div key={e.exec_id}>{e.quantity} @ {e.price} · {e.trade_date}</div>)}</td>
              <td>{shortDate(o.created_at)}</td>
            </tr>))}</tbody>
        </table>
      ) : <p className="text-sm text-[#64748B]" data-testid="orders-none">No orders yet.</p>}
    </div>
  );
}

// ── cpsim04: Transfers (a custodian) — what it received, events, consent, returns; its cash ledger ─────────────────
function TransferTable({ rows, statuses, env, onConsent, showSender = false }) {
  return (
    <table className="w-full text-left text-xs">
      <thead className="text-[#64748B]"><tr><th>Transfer</th>{showSender ? <th>From custodian</th> : null}<th>From</th>
        <th>To</th><th>Rail</th><th>Amount</th><th>Value date</th><th>Status</th><th>Events</th><th /></tr></thead>
      <tbody>{rows.map((t) => {
        const r = transferRow(t, statuses);
        return (
          <tr key={r.id} className="border-t border-[#ece8dd] align-top" data-transfer={r.id}>
            <td>{r.id}<span className="ml-1 rounded border border-[#C5A880] px-1 text-[10px] font-semibold" data-testid="simulated-badge">SIMULATED</span></td>
            {showSender ? <td>{r.sender}</td> : null}
            <td>{r.from}</td><td>{r.to}</td><td>{r.rail}</td><td>{r.amount}</td><td>{r.valueDate}</td>
            <td>{r.status}{r.reason ? ` — ${r.reason}` : ""}{r.returnCode ? ` — ${r.returnCode}` : ""}</td>
            <td>{r.events.map((e, i) => <div key={i}>{e}</div>)}</td>
            <td>{onConsent && consentOffered(env, t)
              ? <button type="button" className={btn} onClick={() => onConsent(r.id)}>Record member consent</button> : null}</td>
          </tr>);
      })}</tbody>
    </table>
  );
}

function CashLedger({ rows }) {
  if (!rows.length) return null;
  return (
    <div data-testid="cash-ledger">
      <h3 className="mt-2 text-sm font-semibold text-navy">Cash ledger (latest moves)</h3>
      <table className="w-full text-left text-xs">
        <thead className="text-[#64748B]"><tr><th>Account</th><th>Move</th><th>Reference</th><th>Amount</th><th>Balance after</th><th>On</th></tr></thead>
        <tbody>{rows.map((m, i) => (
          <tr key={i} className="border-t border-[#ece8dd]"><td>{m.account_id}</td><td>{m.kind}</td><td>{m.source_ref}</td>
            <td>{m.amount}</td><td>{m.balance_after}</td><td>{m.effective_date}</td></tr>))}</tbody>
      </table>
    </div>
  );
}

export function TransfersTab({ id, initialTransfers }) {
  const [env, setEnv, loadError] = useLoaded(initialTransfers, initialTransfers ? null : `${id}/transfers`);
  const [error, setError] = useState(null);
  if (loadError) return <p className="text-sm text-[#9B2335]" data-testid="tab-transfers">{loadError}</p>;
  if (!env) return <p className="text-sm text-[#64748B]" data-testid="tab-transfers">Loading…</p>;
  const rows = Array.isArray(env.rows) ? env.rows : [];
  const statuses = Array.isArray(env.status_labels) ? env.status_labels : [];
  const limits = Array.isArray(env.v1_limits) ? env.v1_limits : [];
  async function consent(transferId) {
    setError(null);
    try {
      await call(`${id}/transfers/${encodeURIComponent(transferId)}/consent`, "POST");
      setEnv(await call(`${id}/transfers`));
    } catch (e) {
      setError(e.message);
    }
  }
  return (
    <div data-testid="tab-transfers" className="space-y-3">
      <p className="text-xs text-[#64748B]">Transfers sent by Hollisworks to this SIMULATED custodian. A person approved
        and sent each one; the clock acknowledges, settles and returns them. Bank details show their last four only.</p>
      {env.behaviour?.consent_required === true ? <p className="text-xs text-navy" data-testid="consent-on">Member consent is required: a received transfer waits for “Record member consent”.</p> : null}
      {limits.length ? <ul className="text-xs text-[#64748B]" data-testid="transfers-v1-limits">{limits.map((l) => <li key={l}>{l}</li>)}</ul> : null}
      {error ? <p className="text-sm text-[#9B2335]">{error}</p> : null}
      {rows.length ? <TransferTable rows={rows} statuses={statuses} env={env} onConsent={consent} />
        : <p className="text-sm text-[#64748B]" data-testid="transfers-none">No transfers yet.</p>}
      <CashLedger rows={Array.isArray(env.cash_movements) ? env.cash_movements : []} />
    </div>
  );
}

// ── cpsim04: Received (a bank) — transfers addressed to it, its cash ledger, the platform accounts linked to it ─────
export function ReceivedTab({ id, initialReceived }) {
  const [env, , loadError] = useLoaded(initialReceived, initialReceived ? null : `${id}/received`);
  if (loadError) return <p className="text-sm text-[#9B2335]" data-testid="tab-received">{loadError}</p>;
  if (!env) return <p className="text-sm text-[#64748B]" data-testid="tab-received">Loading…</p>;
  const rows = Array.isArray(env.rows) ? env.rows : [];
  const links = Array.isArray(env.links) ? env.links : [];
  const statuses = Array.isArray(env.status_labels) ? env.status_labels : [];
  return (
    <div data-testid="tab-received" className="space-y-3">
      <p className="text-xs text-[#64748B]">Transfers SIMULATED custodians sent to this SIMULATED bank — checked against
        its fake routing details (shown as last four only) and credited to its book.</p>
      {rows.length ? <TransferTable rows={rows} statuses={statuses} env={env} showSender />
        : <p className="text-sm text-[#64748B]" data-testid="received-none">Nothing received yet.</p>}
      <CashLedger rows={Array.isArray(env.cash_movements) ? env.cash_movements : []} />
      <div data-testid="bank-links">
        <h3 className="mt-2 text-sm font-semibold text-navy">Linked platform accounts</h3>
        {links.length ? <ul className="text-xs">{links.map((l) => (
          <li key={`${l.org_id}-${l.account_id}`}>{l.org_name} · account {lastFourRef(l.account_number_masked)} → {l.bank_account_id}
            {Object.entries(l.references || {}).map(([k, v]) => ` · ${k.replace(/_/g, " ")} ${lastFourRef(v)}`).join("")}</li>))}</ul>
          : <p className="text-xs text-[#64748B]">No platform account is linked to this bank yet.</p>}
      </div>
    </div>
  );
}

// ── cpsim02: Conformance — run the suite against this counterparty; the last result ──────────────────────────────
export function ConformanceTab({ id, initialConformance }) {
  const [env, setEnv, loadError] = useLoaded(initialConformance, initialConformance ? null : `${id}/conformance`);
  const [key, setKey] = useState("");
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);
  if (loadError) return <p className="text-sm text-[#9B2335]" data-testid="tab-conformance">{loadError}</p>;
  if (!env) return <p className="text-sm text-[#64748B]" data-testid="tab-conformance">Loading…</p>;
  const last = conformanceSummary(env.last);
  async function run() {
    setError(null); setBusy(true);
    try { await call(`${id}/conformance`, "POST", { key }); setEnv(await call(`${id}/conformance`)); setKey(""); }
    catch (e) { setError(e.message); }
    finally { setBusy(false); }
  }
  return (
    <div data-testid="tab-conformance" className="space-y-3">
      <p className="text-xs text-[#64748B]">The suite that will run against the real sandbox the day access arrives —
        every check cites its contract row. Here it runs in-process against this counterparty with the key you paste
        (never stored).</p>
      {canWrite(env) ? (
        <div className="flex flex-wrap gap-2 text-sm" data-testid="conformance-run">
          <input type="password" className="rounded border border-[#E2E8F0] px-2 py-1" placeholder="cpsim_live_…" value={key}
            onChange={(e) => setKey(e.target.value)} autoComplete="off" />
          <button type="button" className={btn} disabled={!key || busy} onClick={run}>Run conformance</button>
        </div>
      ) : null}
      {error ? <p className="text-sm text-[#9B2335]">{error}</p> : null}
      {last ? (
        <div data-testid="conformance-last">
          <p className="text-sm">Last run {shortDate(last.at)}: <span className="text-[#2D6A4F]">{last.passed} passed</span>,{" "}
            <span className={last.failed ? "text-[#9B2335]" : "text-[#64748B]"}>{last.failed} failed</span>.</p>
          {last.failures.length ? <ul className="mt-1 text-xs text-[#9B2335]">{last.failures.map((f) => (
            <li key={f.id}>{f.id} · {f.contract} · {f.label}</li>))}</ul> : null}
        </div>
      ) : <p className="text-sm text-[#64748B]" data-testid="conformance-none">No run yet.</p>}
    </div>
  );
}

export function CounterpartyDetail({ envelope, requests, initialTab = "connection", book = null, connections = null,
  conformance = null, orders = null, initialAdvance = null, transfers = null, received = null }) {
  const [env, setEnv] = useState(envelope);
  const [advance, setAdvance] = useState(initialAdvance);
  const [log, setLog] = useState(requests);
  const [tab, setTab] = useState(initialTab);
  const [issued, setIssued] = useState(null);
  const [error, setError] = useState(null);
  if (!env?.counterparty) return null;
  const id = env.counterparty.id;
  const tabs = tabsFor(env, env.counterparty.institution_type);
  async function act(path, method = "POST", body) {
    setError(null);
    try {
      const out = await call(`${id}/${path}`, method, body);
      setIssued(out.issued && Object.keys(out.issued).length ? out.issued : null);
      if (path === "clock/advance") setAdvance(out);
      setEnv(await call(id));
      setLog(await call(`${id}/requests`));
    } catch (err) {
      setError(err.message);
    }
  }
  const body = {
    connection: <ConnectionTab env={env} d={env} act={act} />, fix: <FixTab env={env} d={env} act={act} />,
    bank: <BankTab env={env} d={env} act={act} />, behaviour: <BehaviourTab env={env} d={env} act={act} />,
    clock: <ClockTab env={env} d={env} act={act} advance={advance} />, activity: <ActivityTab requests={log} />,
    import_export: tabs.find((t) => t.key === "import_export")?.enabled
      ? <ImportExportTab id={id} initialBook={book} />
      : <p className="text-sm text-[#64748B]" data-testid="tab-import-export">{IMPORT_EXPORT_NOTE}</p>,
    connections: <ConnectionsTab id={id} initialConnections={connections} />,
    webhooks: <WebhooksTab env={env} d={env} act={act} requests={log} />,
    conformance: <ConformanceTab id={id} initialConformance={conformance} />,
    orders: <OrdersTab id={id} initialOrders={orders} />,
    transfers: <TransfersTab id={id} initialTransfers={transfers} />,
    received: <ReceivedTab id={id} initialReceived={received} />,
  };
  return (
    <section className={card} data-testid="cp-detail">
      <h2 className="font-serif text-2xl text-navy">{env.counterparty.name}</h2>
      <p className="text-sm text-[#64748B]">{env.institution_type?.label} · {labelOf(vocab(env, "statuses"), env.counterparty.status)}</p>
      <nav className="mt-4 flex flex-wrap gap-4 border-b border-[#ece8dd] text-sm" data-testid="cp-tabs">
        {tabs.map((t) => (
          <button key={t.key} type="button" disabled={!t.enabled} data-tab={t.key}
            title={t.key === "import_export" ? IMPORT_EXPORT_NOTE : undefined}
            className={`pb-2 ${tab === t.key ? "border-b-2 border-[#C5A880] text-navy" : "text-[#64748B]"} disabled:opacity-40`}
            onClick={() => setTab(t.key)}>{t.label}</button>
        ))}
      </nav>
      <div className="mt-4">{issued ? <IssuedSecrets issued={issued} /> : null}
        {error ? <p className="text-sm text-[#9B2335]">{error}</p> : null}
        {body[tab] || null}</div>
    </section>
  );
}

export function OrgAccessPanel({ envelope }) {
  const [env, setEnv] = useState(envelope);
  const [error, setError] = useState(null);
  const rows = env?.rows || [];
  const w = canWrite(env);
  async function toggle(orgId, allowed) {
    setError(null);
    try {
      await call(`org-access/${orgId}`, "PUT", { allowed });
      setEnv(await call("org-access"));
    } catch (err) {
      setError(err.message);
    }
  }
  return (
    <section className={card} data-testid="cp-org-access">
      <h2 className="font-serif text-xl text-navy">Which organizations may connect</h2>
      <p className="text-sm text-[#64748B]">The setting counterparty_sim.allowed — off by default. A test organization may then
        connect freely; a real organization only for households tagged demo.</p>
      {error ? <p className="text-sm text-[#9B2335]">{error}</p> : null}
      <table className="mt-3 w-full text-left text-sm">
        <thead className="text-[#64748B]"><tr><th>Organization</th><th>Test</th><th>Allowed</th><th /></tr></thead>
        <tbody>{rows.map((o) => (
          <tr key={o.id} className="border-t border-[#ece8dd]"><td className="py-1">{o.name}</td>
            <td>{o.is_test ? "Test" : "Real"}</td><td>{o.allowed ? "Yes" : "No"}</td>
            <td>{w ? <button type="button" className={btn} onClick={() => toggle(o.id, !o.allowed)}>
              {o.allowed ? "Disallow" : "Allow"}</button> : null}</td></tr>))}</tbody>
      </table>
    </section>
  );
}

export default function CounterpartiesAdmin({ initialList, initialOrgAccess }) {
  return (
    <div className="space-y-6">
      <SimulatedBanner />
      <CreateWizard envelope={initialList} />
      <CounterpartyList envelope={initialList} />
      {initialOrgAccess ? <OrgAccessPanel envelope={initialOrgAccess} /> : null}
    </div>
  );
}
