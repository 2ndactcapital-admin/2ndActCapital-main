"use client";

/**
 * AccountsList — Portfolio → Accounts (REP5).
 *
 * Every open account with the model it follows, its last drift, its last
 * proposal and its open execution instructions, from GET /proposals/accounts.
 * Read-only: joining and every other change happen on the account page, where
 * the controls render only from the server's envelope. Filters and labels are
 * the envelope's vocabularies.
 */

import { useState } from "react";
import { labelOf, pctText, vocab } from "@/lib/proposals.mjs";

const card = "rounded-[6px] border border-[#ece8dd] bg-white";
const input = "rounded border border-[#E2E8F0] px-3 py-2";

export function AccountsTable({ envelope }) {
  const rows = Array.isArray(envelope?.rows) ? envelope.rows : [];
  const v = vocab(envelope);
  if (!rows.length) {
    return <div className={`${card} p-10 text-center text-sm text-text-muted`}>No accounts match.</div>;
  }
  return (
    <div className={`${card} overflow-x-auto`}>
      <table className="w-full text-sm">
        <thead className="border-b border-[#ece8dd] text-left text-text-muted">
          <tr>
            <th className="px-4 py-3 font-medium">Account</th>
            <th className="px-4 py-3 font-medium">Model</th>
            <th className="px-4 py-3 font-medium">Drift</th>
            <th className="px-4 py-3 font-medium">Last proposal</th>
            <th className="px-4 py-3 font-medium">Open instructions</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id} className="border-b border-[#ece8dd] last:border-0">
              <td className="px-4 py-3">
                <a className="font-medium text-navy hover:underline" href={`/portfolio/accounts/${r.id}`}>
                  {r.account_number_masked}
                </a>
                <span className="ml-2 text-xs text-text-muted">{r.custodian_code}</span>
                {r.is_held_away ? <span className="ml-2 text-xs text-text-muted">held away</span> : null}
                {r.is_discretionary === false ? <span className="ml-2 text-xs text-text-muted">advice only</span> : null}
              </td>
              <td className="px-4 py-3">
                {r.model ? r.model.name : <span className="text-text-muted">Not following a model</span>}
                {r.pending_change ? <span className="ml-2 text-xs text-text-muted">change awaiting approval</span> : null}
              </td>
              <td className="px-4 py-3">{r.last_drift ? pctText(r.last_drift) : "—"}
                {r.last_valuation ? <span className="block text-xs text-text-muted">as of {r.last_valuation}</span> : null}
              </td>
              <td className="px-4 py-3">
                {r.last_proposal ? (
                  <a className="text-navy hover:underline" href={`/portfolio/proposals/${r.last_proposal.id}`}>
                    {labelOf(v.statuses, r.last_proposal.status)} — {labelOf(v.triggers, r.last_proposal.trigger)}
                  </a>
                ) : "—"}
              </td>
              <td className="px-4 py-3">{r.open_instructions ? (
                <a className="text-navy hover:underline" href="/portfolio/instructions">{r.open_instructions}</a>
              ) : "0"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default function AccountsList({ initialEnvelope }) {
  const [env, setEnv] = useState(initialEnvelope);
  const [filters, setFilters] = useState({});
  const [msg, setMsg] = useState(null);
  const v = vocab(env);
  const models = Array.isArray(env?.models) ? env.models : [];

  async function reload(next) {
    setFilters(next);
    const params = new URLSearchParams();
    for (const [k, val] of Object.entries(next)) if (val) params.set(k, String(val));
    const res = await fetch(`/api/proposals/accounts?${params}`);
    const data = await res.json().catch(() => ({}));
    if (res.ok) setEnv(data); else setMsg(data.error || `Reload failed (${res.status})`);
  }
  const set = (k) => (e) => reload({ ...filters, [k]: e.target.value });

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end gap-3 text-sm">
        <label className="flex flex-col gap-1">Show
          <select className={input} value={filters.filter || ""} onChange={set("filter")}>
            <option value="">All open accounts</option>
            {(v.account_filters || []).map((f) => <option key={f.key} value={f.key}>{f.label}</option>)}
          </select>
        </label>
        <label className="flex flex-col gap-1">Model
          <select className={input} value={filters.model_id || ""} onChange={set("model_id")}>
            <option value="">Any</option>
            {models.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
          </select>
        </label>
        <label className="flex flex-col gap-1">Account
          <input className={input} placeholder="Masked number" value={filters.q || ""} onChange={set("q")} />
        </label>
      </div>
      {msg ? <p className="text-sm text-[#9B2335]">{msg}</p> : null}
      <AccountsTable envelope={env} />
    </div>
  );
}
