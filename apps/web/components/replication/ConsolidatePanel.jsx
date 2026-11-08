"use client";

/**
 * ConsolidatePanel — Household X-ray → Consolidate (REP10). Funds overlapping
 * a followed replica (or each other) above the firm's threshold, each with its
 * authority's action: managed → a rep5 proposal into the replica; advise → an
 * advice letter (a one-off proposal); read-only → listed, never traded.
 * Transition tax and break-even are estimates from the X-ray.
 */

import { useEffect, useState } from "react";
import { consolidationControls, labelOf, pct, vocab } from "@/lib/rep10.mjs";
import { consolidateMessage } from "@/lib/bugfix4.mjs";
import { BusyButton, BusyError, useBusy } from "@/components/ui/Busy";
import { jsonOrThrow } from "@/lib/busyAction.mjs";

const card = "rounded-[6px] border border-[#ece8dd] bg-white";
const primary = "rounded bg-navy px-3 py-1 text-white disabled:opacity-50";

export function ConsolidationView({ envelope, onAct, busy = false }) {
  const v = vocab(envelope);
  const lines = Array.isArray(envelope?.lines) ? envelope.lines : [];
  if (!envelope) return null;
  return (
    <div className={`${card} space-y-2 p-4 text-sm`}>
      <h2 className="text-lg font-semibold text-navy">Consolidate overlapping funds</h2>
      <p className="text-text-muted">Overlap threshold {pct(envelope.threshold)}. Figures are estimates.</p>
      {lines.length ? (
        <table className="w-full">
          <thead><tr className="text-left text-text-muted"><th>Fund</th><th>Account</th><th>Overlap</th><th>Transition tax</th><th>Break-even</th><th>Action</th></tr></thead>
          <tbody>{lines.map((l) => {
            const c = consolidationControls(envelope, l);
            return (
              <tr key={`${l.account_id}-${l.fund_security_id}`}>
                <td>{l.label}</td><td>{l.account} ({l.authority})</td>
                <td>{pct(l.replica_overlap?.overlap_weight ?? l.fund_overlap?.overlap_weight)}</td>
                <td>{l.transition_tax?.amount ?? "not estimated"}</td><td>{l.break_even?.years ?? l.break_even?.note ?? "—"}</td>
                <td>{/* CATCHALL1 busy */}{c.consolidate ? <BusyButton busy={busy} className={primary} onClick={() => onAct?.(l)}>{labelOf(v.consolidation_actions, l.action)}</BusyButton> : labelOf(v.consolidation_actions, l.action)}</td>
              </tr>
            );
          })}</tbody>
        </table>
      ) : <p className="text-text-muted">No fund overlaps a replica above the threshold.</p>}
    </div>
  );
}

export default function ConsolidatePanel({ householdId }) {
  const [env, setEnv] = useState(null);
  const [msg, setMsg] = useState(null);
  // BUGFIX4 — the tab said nothing at all on a 403 (and while loading): it now always says why it is empty
  const [state, setState] = useState({ loaded: false, status: null, error: null });
  async function load() {
    const res = await fetch(`/api/replication/consolidation/households/${householdId}`);
    const data = await res.json().catch(() => ({}));
    setState({ loaded: true, status: res.status, error: res.ok ? null : (data.error || `Request failed (${res.status})`) });
    if (res.ok) setEnv(data); else setEnv(null);
  }
  useEffect(() => { load(); }, [householdId]); // eslint-disable-line react-hooks/exhaustive-deps
  // CATCHALL1 busy — one consolidate action at a time (every line's button shares it)
  const action = useBusy();
  async function onAct(l) {
    setMsg(null);
    await action.run(async () => {
      const res = await fetch(`/api/replication/consolidation/households/${householdId}`, { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ account_id: l.account_id, fund_security_id: l.fund_security_id }) });
      const data = await jsonOrThrow(res);
      setMsg(`Created: ${data.action}`);
    });
    await load();
  }
  return (
    <div className="space-y-2">
      {msg ? <p className="text-sm text-text-muted">{msg}</p> : null}
      <BusyError error={action.error} className="block text-sm text-[#9B2335]" />
      {!env && consolidateMessage(state) ? (
        <div className="rounded-[6px] border border-[#ece8dd] bg-white p-10 text-center text-sm text-text-muted">{consolidateMessage(state)}</div>
      ) : null}
      <ConsolidationView envelope={env} onAct={onAct} busy={action.busy} />
    </div>
  );
}
