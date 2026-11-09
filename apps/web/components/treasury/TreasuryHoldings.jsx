"use client";

/**
 * TreasuryHoldings — Household → Treasury → Holdings (CASH05): per sleeve (Cash, Near-term, Reserve, Long-term), each
 * holding's plain-language description, yield with its basis and as-of, liquidity with its cut-off wording, shares /
 * par, value, months of obligations covered and the prospectus (or why there is none). The Long-term row expands to
 * EVERY rung of the ladder: maturity, type, par vs market value, YTM with its basis, the obligation it serves (planned
 * rungs not yet held are marked). Read-only; lib/cashTreasury.mjs fails closed. Light / cream.
 */

import { useState } from "react";
import { holdingGroups, labelText, money, readable } from "@/lib/cashTreasury.mjs";

const card = "rounded-[6px] border border-[#ece8dd] bg-white p-4";
const serif = { fontFamily: "'Spectral', Georgia, serif" };

export function RungTable({ rungs, note }) {
  if (!rungs.length) return <p className="text-xs text-text-muted">{note || "No ladder rungs."}</p>;
  return (
    <table className="mt-2 w-full text-xs" aria-label="Ladder rungs">
      <thead className="text-left text-text-muted"><tr><th className="py-1">Maturity</th><th>Type</th><th>Par</th><th>Market value</th>
        <th>Par vs market</th><th>YTM</th><th>Serves</th></tr></thead>
      <tbody>{rungs.map((r) => (
        <tr key={r.key} className={`border-t border-[#ece8dd] align-top ${r.held ? "" : "text-text-muted"}`} data-held={r.held ? "yes" : "no"}>
          <td className="py-1">{r.maturity}{r.held ? "" : " (planned — not held)"}</td><td>{r.type}</td><td>{money(r.par)}</td>
          <td>{r.market ? money(r.market) : "—"}</td><td>{r.parVsMarket ? money(r.parVsMarket) : "—"}</td>
          <td>{r.ytm}{r.ytmBasis ? <span className="block text-text-muted">{r.ytmBasis}</span> : null}</td>
          <td>{r.serves}</td>
        </tr>))}
      </tbody>
    </table>
  );
}

export default function TreasuryHoldings({ data, expanded = false }) {
  const [open, setOpen] = useState(expanded);
  if (!readable(data)) return null;
  const groups = holdingGroups(data);
  const est = labelText(data, "cash.yield_estimate");
  return (
    <section className="space-y-3" aria-label="Holdings">
      <div className="flex items-baseline gap-2">
        <h3 className="mr-auto text-lg font-semibold text-navy" style={serif}>Holdings</h3>
        <span className="text-sm text-text-muted">Total {money(data.total)}</span>
      </div>
      <p className="text-xs text-text-muted">Sleeves: {data.sleeve_basis}. Months covered: {data.months_basis}.</p>
      {groups.map((g) => (
        <div key={g.key} className={card} aria-label={g.label}>
          <div className="flex flex-wrap items-baseline gap-2">
            <h4 className="mr-auto font-semibold text-navy">{g.label}</h4>
            <span className="text-sm text-navy">{money(g.value)}</span>
            <span className="text-xs text-text-muted">{g.months ? `${g.months} months of obligations` : "no obligations to cover"}</span>
          </div>
          {g.empty ? <p className="text-sm text-text-muted">{g.empty}</p> : (
            <table className="mt-2 w-full text-sm">
              <thead className="text-left text-text-muted"><tr><th className="py-1">Instrument</th><th>Yield</th><th>Liquidity</th>
                <th>Shares / par</th><th>Value</th><th>Months</th></tr></thead>
              <tbody>{g.holdings.map((h) => (
                <tr key={h.key} className="border-t border-[#ece8dd] align-top">
                  <td className="py-1">{h.name}{h.ticker ? ` (${h.ticker})` : ""}
                    <span className="block text-xs text-text-muted">{h.description}</span>
                    {h.note ? <span className="block text-xs text-[#9B2335]">{h.note}</span> : null}
                    {h.prospectus.href ? <a className="block text-xs text-navy underline" href={h.prospectus.href}>Prospectus</a>
                      : <span className="block text-xs text-text-muted">{h.prospectus.text}</span>}</td>
                  <td>{h.yield}{h.yieldBasis ? <span className="block text-xs text-text-muted">{h.yieldBasis}</span> : null}</td>
                  <td className="text-xs">{h.liquidity}</td>
                  <td>{h.shares ? `${h.shares} ${h.units}` : "—"}</td>
                  <td>{money(h.value)}</td>
                  <td>{h.months || "—"}</td>
                </tr>))}
              </tbody>
            </table>)}
          {g.key === "long_term" ? (
            <div className="mt-2">
              <button type="button" className="text-sm text-navy underline" aria-expanded={open} onClick={() => setOpen(!open)}>
                {open ? "Hide the ladder's rungs" : `Show every rung (${g.rungs.length})`}</button>
              {open ? <RungTable rungs={g.rungs} note={g.rungsNote} /> : null}
            </div>) : null}
        </div>))}
      {est ? <p className="text-xs text-text-muted" data-label="cash.yield_estimate">{est}</p> : null}
    </section>
  );
}
