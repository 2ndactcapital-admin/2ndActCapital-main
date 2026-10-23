"use client";

/**
 * InstructionList — Portfolio → Instructions (REP2).
 *
 * Execution instructions from GET /execution/instructions: every trade a
 * person carries out (in this build, every trade). Filters by status, account,
 * executor and due date. The New instruction button and panel render only
 * from `listControls(envelope)` — i.e. only when the envelope says
 * `can_write === true`. Statuses, executors and price kinds are the
 * envelope's vocabularies.
 */

import { useState } from "react";
import { amountText, labelOf, listControls, priceText, securityText, vocab } from "@/lib/execution.mjs";
import NewInstruction from "./NewInstruction";

const card = "rounded-[6px] border border-[#ece8dd] bg-white";

export function InstructionTable({ envelope }) {
  const rows = Array.isArray(envelope?.rows) ? envelope.rows : [];
  const v = vocab(envelope);
  if (!rows.length) {
    return <div className={`${card} p-10 text-center text-sm text-text-muted`}>No instructions match.</div>;
  }
  return (
    <div className={`${card} overflow-x-auto`}>
      <table className="w-full text-sm">
        <thead className="border-b border-[#ece8dd] text-left text-text-muted">
          <tr>
            <th className="px-4 py-3 font-medium">Instruction</th>
            <th className="px-4 py-3 font-medium">Account</th>
            <th className="px-4 py-3 font-medium">Price</th>
            <th className="px-4 py-3 font-medium">Executor</th>
            <th className="px-4 py-3 font-medium">Execute by</th>
            <th className="px-4 py-3 font-medium">Status</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id} className="border-b border-[#ece8dd] last:border-0">
              <td className="px-4 py-3">
                <a className="font-medium text-navy hover:underline" href={`/portfolio/instructions/${r.id}`}>
                  {labelOf(v.sides, r.side)} {amountText(r)} {securityText(r)}
                </a>
              </td>
              <td className="px-4 py-3">
                {r.account?.account_number_masked}
                {r.account?.is_held_away ? <span className="ml-2 text-xs text-text-muted">held away</span> : null}
              </td>
              <td className="px-4 py-3">{priceText(r, v.price_kinds)}</td>
              <td className="px-4 py-3">{labelOf(v.executor_types, r.executor_type)}{r.executor_name ? ` — ${r.executor_name}` : ""}</td>
              <td className="px-4 py-3">{r.execute_by}</td>
              <td className="px-4 py-3">{labelOf(v.statuses, r.status)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function Filters({ envelope, value, onChange }) {
  const v = vocab(envelope);
  const set = (k) => (e) => onChange({ ...value, [k]: e.target.value });
  return (
    <div className="flex flex-wrap items-end gap-3 text-sm">
      <label className="flex flex-col gap-1">Status
        <select className="rounded border border-[#E2E8F0] px-3 py-2" value={value.status || ""} onChange={set("status")}>
          <option value="">Any</option>
          <option value="open">Open</option>
          {(v.statuses || []).map((s) => <option key={s.key} value={s.key}>{s.label}</option>)}
        </select>
      </label>
      <label className="flex flex-col gap-1">Executor
        <select className="rounded border border-[#E2E8F0] px-3 py-2" value={value.executor_type || ""}
          onChange={set("executor_type")}>
          <option value="">Any</option>
          {(v.executor_types || []).map((s) => <option key={s.key} value={s.key}>{s.label}</option>)}
        </select>
      </label>
      <label className="flex flex-col gap-1">Account
        <select className="rounded border border-[#E2E8F0] px-3 py-2" value={value.account_id || ""}
          onChange={set("account_id")}>
          <option value="">Any</option>
          {(envelope?.accounts || []).map((a) => <option key={a.id} value={a.id}>{a.account_number_masked}</option>)}
        </select>
      </label>
      <label className="flex flex-col gap-1">Due from
        <input type="date" className="rounded border border-[#E2E8F0] px-3 py-2" value={value.due_from || ""}
          onChange={set("due_from")} />
      </label>
      <label className="flex flex-col gap-1">Due to
        <input type="date" className="rounded border border-[#E2E8F0] px-3 py-2" value={value.due_to || ""}
          onChange={set("due_to")} />
      </label>
    </div>
  );
}

export default function InstructionList({ initialEnvelope, accounts = [] }) {
  const [env, setEnv] = useState(initialEnvelope);
  const [filters, setFilters] = useState({});
  const [adding, setAdding] = useState(false);
  const [msg, setMsg] = useState(null);
  const controls = listControls(env);

  async function reload(next = filters) {
    setFilters(next);
    const params = new URLSearchParams();
    for (const [k, val] of Object.entries(next)) if (val) params.set(k, String(val));
    const res = await fetch(`/api/execution/instructions?${params}`);
    const data = await res.json().catch(() => ({}));
    if (res.ok) setEnv(data); else setMsg(data.error || `Reload failed (${res.status})`);
  }

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <Filters envelope={{ ...env, accounts }} value={filters} onChange={reload} />
        {controls.create ? (
          <button type="button" className="rounded bg-navy px-4 py-2 text-sm text-white" onClick={() => setAdding(!adding)}>
            New instruction
          </button>
        ) : null}
      </div>
      {msg ? <p className="text-sm text-[#9B2335]">{msg}</p> : null}
      {controls.create && adding ? (
        <NewInstruction envelope={env} accounts={accounts}
          onCreated={async (out) => {
            setAdding(false);
            const first = out?.routing?.instruction_ids?.[0];
            if (first) window.location.href = `/portfolio/instructions/${first}`;
            else await reload();
          }} />
      ) : null}
      <InstructionTable envelope={env} />
    </div>
  );
}
