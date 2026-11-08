"use client";

/**
 * ModelCatalog — REP15. Replication → Model catalog (a tenant: published models provided by a sub-adviser, the
 * sub-advisory agreement acknowledged by version, subscriptions, provided versions to adopt, copy) and Platform →
 * Published models (the provider: the provider record, the publishing gate and its readiness, publishable models and
 * catalog versions, subscribers by status / count / assets, the fee). Controls from lib/modelProvider.mjs (fail
 * closed); labels from the envelope's vocabularies; figures exactly as the server states them. Light / cream only.
 */

import { useState } from "react";
import {
  catalogControls, diffRows, invoiceLines, labelOf, providerControls, readinessLines, refusalText, subscriberRows,
  subscriptionControls, vocab,
} from "@/lib/modelProvider.mjs";

const card = "rounded-[6px] border border-[#ece8dd] bg-white p-5";
const input = "rounded border border-[#E2E8F0] px-3 py-2";
const primary = "rounded bg-navy px-3 py-1 text-white disabled:opacity-50";
const secondary = "rounded border border-[#E2E8F0] px-3 py-1 text-navy disabled:opacity-50";
const th = "px-2 py-1 text-left font-medium text-text-muted";
const td = "px-2 py-1 align-top";
const ok = (b) => (b ? "text-[#2D6A4F]" : "text-[#9B2335]");

export async function send(base, path, method = "GET", body) {
  const res = await fetch(`/api/${base}${path ? `/${path}` : ""}`, {
    method, headers: body ? { "Content-Type": "application/json" } : undefined, body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const err = new Error(data.error || "Request failed");
    err.status = res.status;
    throw err;
  }
  return data;
}

function Notice({ text }) {
  return text ? <p className="mt-2 rounded-[6px] border border-[#ece8dd] bg-[#FAF9F6] p-3 text-sm text-text-muted">{text}</p> : null;
}

// ── Tenant ──────────────────────────────────────────────────────────────────
export function DiffTable({ diff }) {
  const rows = diffRows(diff);
  if (!rows.length) return <p className="text-xs text-text-muted">No weight changed.</p>;
  return (
    <table className="mt-2 w-full text-xs">
      <thead><tr><th className={th}>Security</th><th className={th}>Before</th><th className={th}>After</th><th className={th}>Change</th></tr></thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.key}><td className={td}>{r.label}</td><td className={td}>{r.before}</td><td className={td}>{r.after}</td>
            <td className={`${td} ${r.direction === "down" ? "text-[#9B2335]" : "text-[#2D6A4F]"}`}>{r.change}</td></tr>
        ))}
      </tbody>
    </table>
  );
}

export function CatalogView({ envelope, onAction }) {
  const v = vocab(envelope);
  const items = Array.isArray(envelope?.catalog) ? envelope.catalog : [];
  const subs = Array.isArray(envelope?.subscriptions) ? envelope.subscriptions : [];
  const [copyName, setCopyName] = useState({});
  const act = (fn) => (onAction ? onAction(fn) : undefined);
  return (
    <div className="space-y-6">
      <section className={card}>
        <h2 className="font-serif text-xl text-navy">Published models</h2>
        {items.length === 0 ? <Notice text={`No provided model is available to this firm. ${refusalText(envelope)}`} /> : (
          <ul className="mt-3 space-y-4">
            {items.map((c) => {
              const ctl = catalogControls(envelope, c);
              return (
                <li key={c.catalog_model_id} className="border-t border-[#ece8dd] pt-3">
                  <div className="font-medium text-navy">{c.name}</div>
                  <div className="text-sm text-text-muted">{c.provided_by_label} · {labelOf(v.kinds, c.kind)} · fee {c.fee_bps} bps</div>
                  <p className="mt-1 text-sm">{c.description}</p>
                  <div className="mt-1 text-xs text-text-muted">
                    Versions: {(c.versions || []).map((x) => `${x.catalog_version_no} (${labelOf(v.catalog_statuses, x.status)}, holdings as of ${x.holdings_as_of})`).join("; ")}
                  </div>
                  {c.agreement?.in_force ? (
                    <div className="mt-2 text-xs">
                      Sub-advisory agreement version {c.agreement.agreement?.version_no} ({c.agreement.scope === "org" ? "this firm's own" : "platform default"}):
                      {" "}<span className={ok(c.agreement.current)}>{c.agreement.current ? "acknowledged" : "not acknowledged"}</span>
                      {c.agreement.agreement?.file_href ? <> · <a className="underline" href={c.agreement.agreement.file_href}>Read it</a></> : null}
                    </div>
                  ) : <p className="mt-2 text-xs text-text-muted">No sub-advisory agreement is in force for this firm yet.</p>}
                  {c.subscription ? <div className="mt-1 text-xs">Subscription: {labelOf(v.subscription_statuses, c.subscription.status)}</div> : null}
                  <div className="mt-2 flex gap-2">
                    {ctl.acknowledge ? <button className={secondary} onClick={() => act(() => send("model-catalog", `${c.catalog_model_id}/acknowledge`, "POST", { version_id: c.agreement.agreement.version_id }))}>Acknowledge the agreement</button> : null}
                    {ctl.subscribe ? <button className={primary} onClick={() => act(() => send("model-catalog", `${c.catalog_model_id}/subscribe`, "POST"))}>Request a subscription</button> : null}
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </section>
      <section className={card}>
        <h2 className="font-serif text-xl text-navy">Subscriptions</h2>
        {subs.length === 0 ? <p className="mt-2 text-sm text-text-muted">None.</p> : (
          <ul className="mt-3 space-y-4">
            {subs.map((s) => {
              const ctl = subscriptionControls(envelope, s);
              return (
                <li key={s.id} className="border-t border-[#ece8dd] pt-3 text-sm">
                  <div className="font-medium text-navy">{s.model?.name || "Awaiting approval"}</div>
                  <div className="text-text-muted">{s.model?.provided_by_label} · {labelOf(v.subscription_statuses, s.status)}
                    {s.started_on ? ` · since ${s.started_on}` : ""}{s.ended_on ? ` · ended ${s.ended_on}` : ""}
                    {s.model ? ` · read-only · ${s.accounts} account(s) · on provider version ${s.model.on_catalog_version_no ?? "none yet"}` : ""}</div>
                  <p className="mt-1 text-xs text-text-muted">Operator item: {s.tenant_adv_obligation}</p>
                  <div className="mt-2 flex flex-wrap gap-2">
                    {ctl.adoptVersions.map((vid) => <button key={vid} className={primary} onClick={() => act(() => send("model-catalog", `subscriptions/${s.id}/adopt`, "POST", { version_id: vid }))}>Adopt the new version (a second person approves)</button>)}
                    {ctl.refresh ? <button className={secondary} onClick={() => act(() => send("model-catalog", `subscriptions/${s.id}/refresh`, "POST"))}>Check for a new version</button> : null}
                    {ctl.copy ? (
                      <span className="flex gap-1">
                        <input className={input} placeholder="Holdings of …" value={copyName[s.id] || ""} onChange={(e) => setCopyName({ ...copyName, [s.id]: e.target.value })} />
                        <button className={secondary} onClick={() => act(() => send("model-catalog", `subscriptions/${s.id}/copy`, "POST", { name: copyName[s.id] || "" }))}>Copy into our registry</button>
                      </span>
                    ) : null}
                    {ctl.end ? <button className={secondary} onClick={() => act(() => send("model-catalog", `subscriptions/${s.id}/end`, "POST", { reason: "Ended by the firm" }))}>End the subscription</button> : null}
                  </div>
                </li>
              );
            })}
          </ul>
        )}
      </section>
    </div>
  );
}

// ── Provider ────────────────────────────────────────────────────────────────
export function ProviderView({ envelope, subscribers, onAction }) {
  const v = vocab(envelope);
  const ctl = providerControls(envelope);
  const p = envelope?.provider || {};
  const [f, setF] = useState({ legal_name: p.legal_name || "", sec_file_number: p.sec_file_number || "",
    crd_number: p.crd_number || "", registration_status: p.registration_status || "not_registered",
    effective_date: p.effective_date || "" });
  const [reason, setReason] = useState("");
  const act = (fn) => (onAction ? onAction(fn) : undefined);
  const field = (k, l) => <label className="block text-sm">{l}<input className={`${input} mt-1 w-full`} value={f[k]} disabled={!ctl.saveProvider} onChange={(e) => setF({ ...f, [k]: e.target.value })} /></label>;
  const gate = envelope?.gate || {};
  return (
    <div className="space-y-6">
      <section className={card}>
        <h2 className="font-serif text-xl text-navy">The publishing gate</h2>
        <p className={`mt-1 text-sm ${ok(gate.is_open === true)}`}>{gate.label || "Off"}</p>
        <Notice text={gate.is_open === true ? "" : refusalText(envelope)} />
        <ul className="mt-3 space-y-1 text-sm">
          {readinessLines(envelope).map((i) => <li key={i.key}><span className={ok(i.met)}>{i.met ? "Met" : "Not met"}</span> — {i.label}</li>)}
        </ul>
        {ctl.openGate || ctl.closeGate ? (
          <div className="mt-3 flex gap-2">
            <input className={`${input} flex-1`} placeholder="Reason (recorded)" value={reason} onChange={(e) => setReason(e.target.value)} />
            {ctl.openGate ? <button className={primary} onClick={() => act(() => send("model-provider", "gate/open", "POST", { reason }))}>Open the gate</button> : null}
            {ctl.closeGate ? <button className={secondary} onClick={() => act(() => send("model-provider", "gate/close", "POST", { reason }))}>Close the gate</button> : null}
          </div>
        ) : null}
      </section>
      <section className={card}>
        <h2 className="font-serif text-xl text-navy">The registered adviser entity</h2>
        <div className="mt-3 grid gap-3 sm:grid-cols-2">
          {field("legal_name", "Legal name")}
          {field("sec_file_number", "SEC file number (801-…)")}
          {field("crd_number", "CRD number")}
          {field("effective_date", "Effective date (YYYY-MM-DD)")}
          <label className="block text-sm">Registration status
            <select className={`${input} mt-1 w-full`} value={f.registration_status} disabled={!ctl.saveProvider} onChange={(e) => setF({ ...f, registration_status: e.target.value })}>
              {(v.registration_statuses || []).map((s) => <option key={s.key} value={s.key}>{s.label}</option>)}
            </select>
          </label>
        </div>
        <p className="mt-2 text-xs text-text-muted">ADV Part 2A: {envelope?.adv?.source === "library" ? `version ${envelope.adv.version_no} (${envelope.adv.scope})` : "no published version"}</p>
        {ctl.saveProvider ? <button className={`${primary} mt-3`} onClick={() => act(() => send("model-provider", "provider", "PUT", { ...f, effective_date: f.effective_date || null }))}>Save the record</button> : null}
      </section>
      <section className={card}>
        <h2 className="font-serif text-xl text-navy">Subscribers</h2>
        <p className="text-xs text-text-muted">Status, account counts and model assets only — never a member, an account or a position.</p>
        <table className="mt-2 w-full text-sm">
          <thead><tr><th className={th}>Firm</th><th className={th}>Status</th><th className={th}>Accounts</th><th className={th}>Model assets</th></tr></thead>
          <tbody>
            {subscriberRows(subscribers).map((s) => (
              <tr key={s.subscription_id}><td className={td}>{s.tenant_name}</td><td className={td}>{labelOf(v.subscription_statuses, s.status)}</td>
                <td className={td}>{s.usage.reduce((n, u) => n + (u.account_count || 0), 0)}</td>
                <td className={td}>{s.usage.map((u) => u.model_assets).join(" + ") || "0.00"}</td></tr>
            ))}
          </tbody>
        </table>
      </section>
      <section className={card}>
        <h2 className="font-serif text-xl text-navy">Operator items</h2>
        <ul className="mt-2 list-disc pl-5 text-sm text-text-muted">{(envelope?.operator_items || []).map((t) => <li key={t}>{t}</li>)}</ul>
      </section>
    </div>
  );
}

export function InvoiceView({ invoice }) {
  return (
    <table className="w-full text-sm">
      <thead><tr><th className={th}>Line</th><th className={th}>bps</th><th className={th}>Days</th><th className={th}>Average daily model assets</th><th className={th}>Amount</th></tr></thead>
      <tbody>{invoiceLines(invoice).map((l, i) => <tr key={i}><td className={td}>{l.label}</td><td className={td}>{l.fee_bps}</td><td className={td}>{l.days}</td><td className={td}>{l.average_daily_assets}</td><td className={td}>{l.amount}</td></tr>)}</tbody>
    </table>
  );
}

export function CatalogHome({ initial }) {
  const [env, setEnv] = useState(initial);
  const [error, setError] = useState(null);
  const onAction = async (fn) => {
    setError(null);
    try {
      await fn();
      setEnv(await send("model-catalog", ""));
    } catch (e) {
      setError(e.message);
    }
  };
  return <>{error ? <Notice text={error} /> : null}<CatalogView envelope={env} onAction={onAction} /></>;
}

export function ProviderHome({ initial, initialSubscribers }) {
  const [env, setEnv] = useState(initial);
  const [subs, setSubs] = useState(initialSubscribers);
  const [error, setError] = useState(null);
  const onAction = async (fn) => {
    setError(null);
    try {
      await fn();
      setEnv(await send("model-provider", ""));
      setSubs(await send("model-provider", "subscribers"));
    } catch (e) {
      setError(e.message);
    }
  };
  return <>{error ? <Notice text={error} /> : null}<ProviderView envelope={env} subscribers={subs} onAction={onAction} /></>;
}
