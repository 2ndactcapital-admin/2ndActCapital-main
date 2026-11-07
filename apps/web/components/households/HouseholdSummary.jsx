"use client";

/**
 * HouseholdSummary — BUGFIX4: the household's net worth on its OWN page (gross, liabilities, net, as-of), its top
 * holdings, and Recalculate for an org admin. Every position counted ONCE at the household's share of its owner
 * (services/household_total). Recalculate renders only from the server's envelope (lib/bugfix4.mjs, fails closed).
 */

import { useEffect, useState } from "react";
import { householdControls, labelOf, totalLines } from "@/lib/bugfix4.mjs";
import { money } from "@/lib/heldAway.mjs";
import { OwnersTable, SplitTable, UnclassifiedList } from "@/components/networth/NetWorthDetails";

const card = "rounded-[6px] border border-[#ece8dd] bg-white p-4 text-sm";
const secondary = "rounded border border-navy px-3 py-1 text-navy disabled:opacity-50";

export function SummaryView({ envelope, onRecalculate, busy, note }) {
  const t = totalLines(envelope?.total);
  if (!t) return null;
  const c = householdControls(envelope);
  const v = envelope?.vocabularies || {};
  const top = Array.isArray(envelope?.total?.top_holdings) ? envelope.total.top_holdings : [];
  return (
    <div className={card}>
      <div className="flex flex-wrap items-baseline gap-6">
        <div><p className="text-text-muted">Gross</p><p className="text-xl text-navy">{money(t.gross)}</p></div>
        <div><p className="text-text-muted">Liabilities</p><p className="text-xl text-navy">{money(t.liabilities)}</p></div>
        <div><p className="text-text-muted">Net worth</p><p className="text-xl font-semibold text-navy">{money(t.net)}</p></div>
        <div><p className="text-text-muted">As of</p><p>{t.asOf || "—"}</p></div>
        {c.recalculate ? <button type="button" className={secondary} disabled={busy} onClick={onRecalculate}>{busy ? "Recalculating…" : "Recalculate"}</button> : null}
      </div>
      {t.unclassified ? <p className="mt-2 text-xs text-text-muted">Includes {money(t.unclassified)} not yet classified into an allocation bucket.</p> : null}
      {t.unvalued ? <p className="mt-1 text-xs text-[#9B2335]">{t.unvalued} holding(s) have no value and are not counted.</p> : null}
      {note ? <p className="mt-1 text-xs text-text-muted">{note}</p> : null}
      {/* ROLLUP2 — Family / Charitable / Household total, the Owners list (each member's own card figure) and the
          valued holdings with no allocation bucket */}
      <SplitTable total={envelope?.total} vocabularies={v} />
      <OwnersTable total={envelope?.total} vocabularies={v} />
      <UnclassifiedList netWorth={envelope?.total} vocabularies={v} />
      {top.length ? (
        <table className="mt-3 w-full">
          <thead className="text-left text-text-muted"><tr><th className="py-1">Top holdings</th><th>Value</th><th>Positions</th></tr></thead>
          <tbody>{top.map((h) => (
            <tr key={h.key} className="border-t border-[#ece8dd]"><td className="py-1">{h.label}</td><td>{money(h.value)}</td><td>{h.positions}</td></tr>
          ))}</tbody>
        </table>
      ) : null}
      <p className="mt-2 text-xs text-text-muted" title={envelope?.total?.basis}>{labelOf(v.basis_labels, "entered_value")} unless a valuation is recorded.</p>
    </div>
  );
}

export default function HouseholdSummary({ householdId }) {
  const [env, setEnv] = useState(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState(null);
  async function load() {
    const res = await fetch(`/api/households/${householdId}/summary`, { cache: "no-store" });
    const data = await res.json().catch(() => ({}));
    setEnv(res.ok ? data : null);
  }
  useEffect(() => { load(); }, [householdId]); // eslint-disable-line react-hooks/exhaustive-deps
  async function onRecalculate() {
    setBusy(true);
    setNote(null);
    try {
      const res = await fetch(`/api/admin/households/${householdId}/recalculate`, { method: "POST" });
      const data = await res.json().catch(() => ({}));
      setNote(res.ok ? `Recalculated (${data.status}).` : (data.error || `Recalculate failed (${res.status})`));
      await load();
    } finally { setBusy(false); }
  }
  return <SummaryView envelope={env} onRecalculate={onRecalculate} busy={busy} note={note} />;
}
