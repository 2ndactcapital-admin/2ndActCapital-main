"use client";

import { useState } from "react";
import { can, labelOf, vocab } from "./sourceLabel.mjs";

// MARKETDATA1 — Super Admin: providers (status, licence facts, budgets, calls) and the platform
// rank per data type. Every control renders only when the server's envelope says can_write AND
// lists the field as editable — a missing envelope fails CLOSED.
const card = "rounded-[6px] border border-[#ece8dd] bg-white p-5";

export function ProvidersView({ envelope, rankEnvelope, onAct }) {
  const providers = envelope?.providers || [];
  const editable = vocab(envelope, "editable");
  const write = can(envelope, "can_write") && editable.length > 0;
  const rankWrite = can(rankEnvelope, "can_write") && vocab(rankEnvelope, "rank_editable").includes("providers");
  const statuses = vocab(envelope, "provider_statuses");
  const md = vocab(envelope, "member_display");
  const ret = vocab(envelope, "retention");
  const types = vocab(envelope, "data_types");
  return (
    <div className="space-y-6">
      <section className={card}>
        <h2 className="font-serif text-xl text-navy">Providers</h2>
        <table className="mt-3 w-full text-sm">
          <thead className="text-left text-text-muted">
            <tr><th>Provider</th><th>Kind</th><th>Status</th><th>Internal use</th><th>Shown to members</th>
              <th>Redistribution</th><th>After cancellation</th><th>Budget / day</th><th>Calls today</th>
              {write ? <th /> : null}</tr>
          </thead>
          <tbody>
            {providers.map((p) => (
              <tr key={p.key} className="border-t border-[#E2E8F0]" data-testid="provider-row">
                <td title={p.terms_note || ""}>{p.name}<span className="block text-xs text-text-muted">{p.key}
                  {p.attribution ? ` · ${p.attribution}` : ""}{p.confirmed_on ? ` · confirmed ${p.confirmed_on}` : ""}</span></td>
                <td>{labelOf(vocab(envelope, "provider_kinds"), p.kind)}</td>
                <td>{labelOf(statuses, p.status)}{p.connected ? "" : " (no live module)"}</td>
                <td>{p.lic_internal_use ? "Yes" : "No"}</td>
                <td>{labelOf(md, p.lic_member_display)}</td>
                <td>{p.lic_redistribution ? "Yes" : "No"}</td>
                <td>{labelOf(ret, p.retention_after_cancel)}</td>
                <td>{p.daily_call_budget ?? "No limit"}</td>
                <td>{p.calls_today}{p.budget_left_today != null ? ` (${p.budget_left_today} left)` : ""}</td>
                {write ? (
                  <td>
                    {editable.includes("status") ? (
                      <select aria-label={`Status of ${p.key}`} defaultValue={p.status}
                        onChange={(e) => onAct?.("provider", { key: p.key, changes: { status: e.target.value } })}>
                        {statuses.map((s) => <option key={s.key} value={s.key}>{s.label}</option>)}
                      </select>
                    ) : null}
                    {editable.includes("daily_call_budget") ? (
                      <button type="button" className="ml-2 text-navy underline"
                        onClick={() => {
                          const v = window.prompt(`Daily call budget for ${p.key} (blank = no limit)`, p.daily_call_budget ?? "");
                          if (v !== null) onAct?.("provider", { key: p.key, changes: { daily_call_budget: v === "" ? null : Number(v) } });
                        }}>Set budget</button>
                    ) : null}
                  </td>
                ) : null}
              </tr>
            ))}
          </tbody>
        </table>
      </section>
      <section className={card}>
        <h2 className="font-serif text-xl text-navy">Platform rank per data type</h2>
        <p className="text-sm text-text-muted">The first allowed source wins; a source whose licence does not allow the purpose is skipped.</p>
        <ul className="mt-3 space-y-2 text-sm">
          {(rankEnvelope?.ranks || []).map((r) => (
            <li key={r.data_type} data-testid="rank-row">
              <span className="font-medium">{labelOf(types, r.data_type)}</span>: {r.platform.join(" → ") || "(no source)"}
              {rankWrite ? (
                <button type="button" className="ml-2 text-navy underline"
                  onClick={() => {
                    const v = window.prompt(`Providers for ${r.data_type}, in order (comma separated)`, r.platform.join(", "));
                    if (v !== null) onAct?.("platform_rank", { data_type: r.data_type, providers: v.split(",").map((x) => x.trim()).filter(Boolean) });
                  }}>Edit rank</button>
              ) : null}
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}

export default function ProvidersAdmin({ initialEnvelope, initialRank }) {
  const [env, setEnv] = useState(initialEnvelope);
  const [rank, setRank] = useState(initialRank);
  const [error, setError] = useState(null);
  async function reload() {
    const [a, b] = await Promise.all([fetch("/api/admin/security-data/providers"), fetch("/api/admin/security-data/rank")]);
    if (a.ok) setEnv(await a.json());
    if (b.ok) setRank(await b.json());
  }
  async function onAct(kind, arg) {
    setError(null);
    const req = kind === "provider"
      ? fetch(`/api/admin/security-data/providers/${encodeURIComponent(arg.key)}`, { method: "PUT", body: JSON.stringify(arg.changes) })
      : fetch(`/api/admin/security-data/rank/${encodeURIComponent(arg.data_type)}`, { method: "PUT", body: JSON.stringify({ providers: arg.providers }) });
    const res = await req;
    if (!res.ok) setError((await res.json().catch(() => ({}))).error || "Request failed");
    await reload();
  }
  return (
    <div>
      {error ? <p className="mb-3 text-sm text-[#9B2335]">{error}</p> : null}
      <ProvidersView envelope={env} rankEnvelope={rank} onAct={onAct} />
    </div>
  );
}
