"use client";

/**
 * CoverageCard + EntityTable — cash01's coverage card (months, liquid, covered through, next gap, flags) and the
 * per-entity coverage table, moved out of TreasuryPanel by CASH05 (unchanged) so the Treasury OVERVIEW renders the
 * same card from its own read (an entity filter passes that entity's coverage through). Re-exported by TreasuryPanel.
 */

import {
  coveredText, estimateLabel, flagLabels, gapText, money, monthsText,
} from "@/lib/treasury.mjs";

const card = "rounded-[6px] border border-[#ece8dd] bg-white p-4";

export function CoverageCard({ env }) {
  const c = env?.coverage;
  if (!c) return null;
  const est = estimateLabel(env);
  return (
    <section className={card} aria-label="Cash coverage">
      <h2 className="text-lg font-semibold text-navy" style={{ fontFamily: "'Spectral', Georgia, serif" }}>Cash coverage</h2>
      <div className="mt-2 grid grid-cols-1 gap-3 sm:grid-cols-3">
        <div><p className="text-sm text-text-muted">Months of coverage</p><p className="text-2xl text-navy">{monthsText(c)}</p>
          <p className="text-xs text-text-muted">Minimum {c.min_months} months</p></div>
        <div><p className="text-sm text-text-muted">Liquid balance</p><p className="text-2xl text-navy">{money(c.liquid)}</p>
          <p className="text-xs text-text-muted">{c.liquid_basis}</p></div>
        <div><p className="text-sm text-text-muted">{coveredText(c)}</p><p className="text-sm text-[#334155]">{gapText(c)}</p></div>
      </div>
      {c.flags?.length ? <p className="mt-2 text-sm text-[#9B2335]">{flagLabels(env, c.flags).join(" · ")}</p> : null}
      <p className="mt-2 text-xs text-text-muted">As of {c.as_of}; average monthly outflow {money(c.monthly_outflow)} (next 12 months).</p>
      {est ? <p className="mt-2 text-xs text-text-muted" data-label="cash.coverage_estimate">{est}</p> : null}
    </section>
  );
}

export function EntityTable({ env }) {
  const rows = Array.isArray(env?.entities) ? env.entities : [];
  if (!rows.length) return <p className="text-sm text-text-muted">No entity of this household has cash or obligations yet.</p>;
  return (
    <table className="w-full text-sm" aria-label="Coverage by entity">
      <thead className="text-left text-text-muted"><tr><th className="py-1">Entity</th><th>Liquid</th><th>Months</th>
        <th>Covered through</th><th>Next gap</th><th>Flags</th></tr></thead>
      <tbody>{rows.map((r) => (
        <tr key={r.entity_id} className="border-t border-[#ece8dd] align-top">
          <td className="py-1">{r.display_name}</td>
          <td>{r.has_cash ? money(r.liquid) : "No cash"}</td>
          <td>{monthsText(r)} <span className="block text-xs text-text-muted">min {r.min_months}{r.min_basis ? ` (${r.min_basis})` : ""}</span></td>
          <td>{r.covered_beyond_horizon ? `beyond ${r.covered_through}` : r.covered_through}</td>
          <td>{r.next_gap ? `${r.next_gap.name}, ~${money(r.next_gap.amount)}, ${r.next_gap.date}` : "—"}</td>
          <td className="text-[#9B2335]">{flagLabels(env, r.flags).join(" · ")}</td>
        </tr>))}
      </tbody>
    </table>
  );
}
