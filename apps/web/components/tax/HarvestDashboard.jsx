"use client";

/**
 * HarvestDashboard — Portfolio → Tax → Harvest (REP6): harvestable losses
 * today with their estimated value, candidates skipped and why, the gain
 * budget used, open household wash-sale windows with their end dates, and the
 * loss bank by member. Read-only except "Refresh loss bank" / "Scan now",
 * which render only from `pageControls(envelope)`. Every harvest is a rep5
 * proposal a person approves — nothing here trades.
 */

import { useState } from "react";
import { labelOf, money, pageControls, vocab } from "@/lib/tax.mjs";
import { Estimate, card, post, secondary } from "@/components/tax/common";

export default function HarvestDashboard({ envelope, onReload }) {
  const [msg, setMsg] = useState(null);
  const d = envelope?.dashboard || {};
  const v = vocab(envelope);
  const c = pageControls(envelope);
  const h = d.harvestable || {};
  const rows = Array.isArray(h.rows) ? h.rows : [];
  const skipped = Array.isArray(d.skipped) ? d.skipped : [];
  const budgets = Array.isArray(d.gain_budgets) ? d.gain_budgets : [];
  const windows = Array.isArray(d.wash_windows) ? d.wash_windows : [];
  const bank = Array.isArray(d.loss_bank) ? d.loss_bank : [];

  async function act(path) {
    setMsg(null);
    try { await post(path); if (onReload) await onReload(); setMsg("Done."); } catch (e) { setMsg(e.message); }
  }

  return (
    <div className="space-y-5">
      <div className={`${card} p-6 text-sm`}>
        <h2 className="text-lg font-semibold text-navy">Harvestable losses today</h2>
        <p className="mt-1 text-text-muted">As of {d.as_of || "—"} · loss {money(h.loss)} · estimated value {money(h.est_value)} (estimate, {h.estimated ?? 0} valued, {h.not_estimated ?? 0} not estimated)</p>
        <div className="mt-2 flex gap-2">
          {c.refreshLossBank ? <button type="button" className={secondary} onClick={() => act("tax/loss-bank/refresh")}>Refresh loss bank</button> : null}
        </div>
        {rows.length ? (
          <table className="mt-3 w-full">
            <thead className="text-left text-text-muted"><tr><th className="py-1">Account</th><th>Security</th><th>Lot</th><th>Loss</th><th>Value</th><th>Outcome</th></tr></thead>
            <tbody>{rows.map((r) => (
              <tr key={r.id} className="border-t border-[#ece8dd] align-top">
                <td className="py-1">{r.account}</td><td>{r.security || r.global_security_id}</td>
                <td>{r.quantity} sh · {labelOf(v.terms, r.term)}</td>
                <td>{money(r.unrealized)}<span className="block text-xs text-text-muted">net of est. trading cost {money(r.net_loss)}</span></td>
                <td><Estimate value={r.est_tax_value} note={r.value_note} /></td>
                <td>{r.outcome === "proposed" ? <>Proposed{r.replacement ? ` → ${r.replacement}` : ""}{r.proposal_id ? <a className="block text-xs text-navy underline" href={`/portfolio/proposals/${r.proposal_id}`}>Proposal</a> : null}</>
                  : labelOf(v.skip_reasons, r.skip_reason)}</td>
              </tr>
            ))}</tbody>
          </table>
        ) : <p className="mt-2 text-text-muted">No harvestable loss on the latest scans.</p>}
      </div>

      <div className={`${card} p-6 text-sm`}>
        <h2 className="text-lg font-semibold text-navy">Candidates skipped, and why</h2>
        {skipped.length ? (
          <ul className="mt-2 space-y-1">{skipped.map((r) => (
            <li key={r.id}><span className="font-medium">{labelOf(v.skip_reasons, r.skip_reason)}</span> — {r.account}, {r.security || r.global_security_id}: {r.skip_detail}</li>
          ))}</ul>
        ) : <p className="mt-2 text-text-muted">Nothing skipped.</p>}
      </div>

      <div className={`${card} p-6 text-sm`}>
        <h2 className="text-lg font-semibold text-navy">Gain budget used</h2>
        <table className="mt-2 w-full">
          <thead className="text-left text-text-muted"><tr><th className="py-1">Account</th><th>Engine</th><th>Budget</th><th>Realized this year</th><th>Room</th><th>Last scan</th></tr></thead>
          <tbody>{budgets.map((b) => (
            <tr key={b.account_id} className="border-t border-[#ece8dd]">
              <td className="py-1"><a className="text-navy underline" href={`/portfolio/accounts/${b.account_id}`}>{b.account}</a></td>
              <td>{labelOf(v.harvesting_engines, b.engine) || b.engine}</td>
              <td>{b.gain_budget ? money(b.gain_budget) : "Unlimited"}</td><td>{money(b.realized_ytd)}</td>
              <td>{b.room !== null && b.room !== undefined ? money(b.room) : "—"}</td>
              <td>{b.last_run ? `${b.last_run.run_date} — ${labelOf(v.run_outcomes, b.last_run.outcome)}` : "Not scanned yet"}</td>
            </tr>
          ))}</tbody>
        </table>
      </div>

      <div className={`${card} p-6 text-sm`}>
        <h2 className="text-lg font-semibold text-navy">Open wash-sale windows</h2>
        {windows.length ? (
          <ul className="mt-2 space-y-1">{windows.map((w, i) => (
            <li key={i}>{w.kind === "loss_sale" ? "Loss sale" : "Buy"} of {w.label} in {w.account} ({w.pending ? `pending ${w.from} to ${w.to}` : w.to}) — blocks {w.blocks} until {w.window_ends}; clear from {w.clear_from}</li>
          ))}</ul>
        ) : <p className="mt-2 text-text-muted">No open window.</p>}
      </div>

      <div className={`${card} p-6 text-sm`}>
        <h2 className="text-lg font-semibold text-navy">Loss bank by member</h2>
        {bank.length ? bank.map((b) => {
          const s = b.summary || {};
          const t = s.totals || {};
          const n = s.netting || {};
          const val = s.tax_value || {};
          return (
            <div key={`${b.subject_type}:${b.subject_id}`} className="mt-3 border-t border-[#ece8dd] pt-2">
              <p className="font-medium text-navy">{b.name || b.subject_id} <span className="text-text-muted">({(b.accounts || []).join(", ")})</span></p>
              <p>Realized {s.tax_year}: short-term gains {money(t.st_gains)}, losses {money(t.st_losses)} · long-term gains {money(t.lt_gains)}, losses {money(t.lt_losses)}</p>
              <p>Carried in: ST {money(s.carryforward_in?.st)}, LT {money(s.carryforward_in?.lt)} · against ordinary income {money(n.ordinary_offset)} · carried out: ST {money(s.carryforward_out?.st)}, LT {money(s.carryforward_out?.lt)}</p>
              <p>Harvested: ST {money(s.harvested?.st)}, LT {money(s.harvested?.lt)} · value <Estimate value={val.estimated ? val.harvested_value : null} note={val.label} rates={val.rates} /></p>
            </div>
          );
        }) : <p className="mt-2 text-text-muted">No member has an account following a model.</p>}
        <p className="mt-3 text-xs text-text-muted">After-tax performance: {d.not_estimated?.after_tax_performance || "not estimated — needs prices"} · tracking-error cost of a harvest: {d.not_estimated?.tracking_error_cost || "not estimated — needs prices"}</p>
      </div>
      {msg ? <p className="text-sm text-text-muted">{msg}</p> : null}
    </div>
  );
}
