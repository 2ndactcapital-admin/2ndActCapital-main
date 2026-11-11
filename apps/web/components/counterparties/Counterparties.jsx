"use client";

import { useState } from "react";
import {
  IMPORT_EXPORT_NOTE, SIMULATED_NOTE, canWrite, groupsOf, labelOf, liveKey, shortDate, tabsFor, typeOf, vocab,
} from "@/lib/counterparties.mjs";

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
      </dl>
      {canWrite(env) && outcomes.length ? (
        <div className="mt-3 flex flex-wrap gap-2" data-testid="behaviour-actions">
          {outcomes.filter((o) => o.key === "accept").map((o) => (
            <button key={o.key} type="button" className={btn}
              onClick={() => act("behaviour", "PUT", { default_outcome: o.key })}>{o.label}</button>))}
          <button type="button" className={btn}
            onClick={() => act("behaviour", "PUT", { webhooks_enabled: !b.webhooks_enabled })}>
            {b.webhooks_enabled ? "Webhooks off" : "Webhooks on"}</button>
        </div>
      ) : null}
    </div>
  );
}

function ClockTab({ env, d, act }) {
  const clock = d.clock || [];
  return (
    <div data-testid="tab-clock">
      <p className="text-sm">Business date: <span className="font-semibold text-navy">{clock[0]?.business_date || ""}</span></p>
      {canWrite(env) ? <button type="button" className={`${btn} mt-2`} onClick={() => act("clock/advance")}>
        Advance one business day</button> : null}
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

export function CounterpartyDetail({ envelope, requests, initialTab = "connection" }) {
  const [env, setEnv] = useState(envelope);
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
      setEnv(await call(id));
      setLog(await call(`${id}/requests`));
    } catch (err) {
      setError(err.message);
    }
  }
  const body = {
    connection: <ConnectionTab env={env} d={env} act={act} />, fix: <FixTab env={env} d={env} act={act} />,
    bank: <BankTab env={env} d={env} act={act} />, behaviour: <BehaviourTab env={env} d={env} act={act} />,
    clock: <ClockTab env={env} d={env} act={act} />, activity: <ActivityTab requests={log} />,
    import_export: <p className="text-sm text-[#64748B]" data-testid="tab-import-export">{IMPORT_EXPORT_NOTE}</p>,
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
