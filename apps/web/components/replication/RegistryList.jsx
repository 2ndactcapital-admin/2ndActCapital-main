"use client";

/**
 * RegistryList — Portfolio → Replication (REP1).
 *
 * The org's registry of funds to replicate, from GET /replication/registry.
 * Display names are always "Holdings of <ticker>" (the server's own
 * display_name); a licensed index name never appears here. The Add button and
 * the Add panel render only from `registryControls(envelope)` — i.e. only when
 * the envelope says `can_write === true`. Instrument classes, statuses and
 * sources are the envelope's vocabularies.
 */

import { useState } from "react";
import {
  constituentsCell, disclosure, displayName, labelOf, registryControls, termsStatus,
} from "@/lib/replication.mjs";

const card = "rounded-[6px] border border-[#ece8dd] bg-white";

export function RegistryTable({ envelope }) {
  const rows = Array.isArray(envelope?.rows) ? envelope.rows : [];
  const v = envelope?.vocabularies || {};
  if (!rows.length) {
    return <div className={`${card} p-10 text-center text-sm text-text-muted`}>No funds in the registry yet.</div>;
  }
  return (
    <div className={`${card} overflow-x-auto`}>
      <table className="w-full text-sm">
        <thead className="border-b border-[#ece8dd] text-left text-text-muted">
          <tr>
            <th className="px-4 py-3 font-medium">Name</th>
            <th className="px-4 py-3 font-medium">Class</th>
            <th className="px-4 py-3 font-medium">Issuer</th>
            <th className="px-4 py-3 font-medium">Holdings as of</th>
            <th className="px-4 py-3 font-medium">Constituents (resolved / total)</th>
            <th className="px-4 py-3 font-medium">Terms</th>
            <th className="px-4 py-3 font-medium">Last refreshed</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id} className="border-b border-[#ece8dd] last:border-0">
              <td className="px-4 py-3">
                <a className="font-medium text-navy hover:underline" href={`/portfolio/replication/${r.id}`}>
                  {r.display_name}
                </a>
                {r.status !== "active" ? (
                  <span className="ml-2 text-xs text-text-muted">{labelOf(v.entry_statuses, r.status)}</span>
                ) : null}
              </td>
              <td className="px-4 py-3">{labelOf(v.instrument_classes, r.instrument_class)}</td>
              <td className="px-4 py-3">{r.issuer || "—"}</td>
              <td className="px-4 py-3">
                {r.latest_as_of || "—"}
                {r.latest_status === "needs_review" ? <span className="ml-2 text-xs text-[#9B2335]">needs review</span> : null}
              </td>
              <td className="px-4 py-3">{constituentsCell(r)}</td>
              <td className="px-4 py-3">{termsStatus(r, v.version_statuses)}</td>
              <td className="px-4 py-3 text-text-muted">{r.last_refreshed ? r.last_refreshed.slice(0, 16).replace("T", " ") : "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function AddSecurity({ envelope, onAdded }) {
  const v = envelope?.vocabularies || {};
  const classes = (v.instrument_classes || []).filter((c) => c.enabled === true);
  const [query, setQuery] = useState({ ticker: "", cusip: "" });
  const [cls, setCls] = useState(classes[0]?.key || "");
  const [found, setFound] = useState(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState(null);

  async function lookup(e) {
    e.preventDefault();
    setBusy(true); setMsg(null); setFound(null);
    try {
      const params = new URLSearchParams();
      if (query.ticker.trim()) params.set("ticker", query.ticker.trim());
      if (query.cusip.trim()) params.set("cusip", query.cusip.trim());
      const res = await fetch(`/api/replication/lookup?${params}`);
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.error || `Lookup failed (${res.status})`);
      setFound(data);
    } catch (err) {
      setMsg(err.message);
    } finally {
      setBusy(false);
    }
  }

  async function run() {
    setBusy(true); setMsg(null);
    try {
      const res = await fetch("/api/replication/registry", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ticker: found.ticker, cusip: found.cusip || undefined, instrument_class: cls }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.error || `Add failed (${res.status})`);
      onAdded?.(data);
    } catch (err) {
      setMsg(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className={`${card} p-6`}>
      <h2 className="text-lg font-semibold text-navy">Add a fund</h2>
      <form onSubmit={lookup} className="mt-4 flex flex-wrap items-end gap-3 text-sm">
        <label className="flex flex-col gap-1">Ticker
          <input className="rounded border border-[#E2E8F0] px-3 py-2" value={query.ticker}
            onChange={(e) => setQuery({ ...query, ticker: e.target.value })} />
        </label>
        <label className="flex flex-col gap-1">or CUSIP
          <input className="rounded border border-[#E2E8F0] px-3 py-2" value={query.cusip}
            onChange={(e) => setQuery({ ...query, cusip: e.target.value })} />
        </label>
        <button type="submit" disabled={busy} className="rounded bg-navy px-4 py-2 text-white disabled:opacity-50">
          Find
        </button>
      </form>
      {found ? (
        <div className="mt-5 space-y-2 text-sm">
          <p><span className="font-medium text-navy">{found.display_name || displayName(found.ticker)}</span>
            {found.name && found.name !== found.ticker ? <span className="text-text-muted"> — {found.name}</span> : null}</p>
          <p className="text-text-muted">
            SEC: {found.sec ? `CIK ${found.sec.cik}, series ${found.sec.series_id}, class ${found.sec.class_id}` : "not in the SEC fund ticker file"}
            {" · "}Holdings source: {found.holdings_source?.label || found.holdings_source?.error || "—"}
          </p>
          <p className="text-xs text-text-muted">{found.disclosure || disclosure(found.ticker)}</p>
          {found.existing_entry_id ? (
            <p>Already in the registry — <a className="text-navy underline" href={`/portfolio/replication/${found.existing_entry_id}`}>open it</a>.</p>
          ) : (
            <div className="flex items-end gap-3">
              <label className="flex flex-col gap-1">Class
                <select className="rounded border border-[#E2E8F0] px-3 py-2" value={cls} onChange={(e) => setCls(e.target.value)}>
                  {classes.map((c) => <option key={c.key} value={c.key}>{c.label}</option>)}
                </select>
              </label>
              <button type="button" onClick={run} disabled={busy || !cls || !found.holdings_source?.key}
                className="rounded bg-navy px-4 py-2 text-white disabled:opacity-50">
                Add and read its holdings and terms
              </button>
            </div>
          )}
        </div>
      ) : null}
      {msg ? <p className="mt-3 text-sm text-[#9B2335]">{msg}</p> : null}
    </div>
  );
}

export default function RegistryList({ initialEnvelope }) {
  const [env, setEnv] = useState(initialEnvelope);
  const [adding, setAdding] = useState(false);
  const controls = registryControls(env);

  async function reload() {
    const res = await fetch("/api/replication/registry");
    if (res.ok) setEnv(await res.json());
  }

  return (
    <div className="space-y-6">
      {controls.add ? (
        <div className="flex justify-end">
          <button type="button" onClick={() => setAdding(!adding)} className="rounded bg-navy px-4 py-2 text-sm text-white">
            {adding ? "Close" : "Add a fund"}
          </button>
        </div>
      ) : null}
      {controls.add && adding ? (
        <AddSecurity envelope={env} onAdded={(data) => { setAdding(false); reload();
          if (data?.entry?.id) window.location.href = `/portfolio/replication/${data.entry.id}`; }} />
      ) : null}
      <RegistryTable envelope={env} />
    </div>
  );
}
