"use client";

/**
 * AccountTaxPanel — the account page's Tax panel (REP6): the household's
 * profile summary (combined rates as an estimate, with the as-of), the loss
 * bank, the household's open wash-sale windows, and the last harvest run with
 * its candidates. Rendered only when the server returned the panel (a caller
 * without view_tax gets a 403 and sees no panel). "Scan now" renders only from
 * the envelope (can_write AND the action).
 */

import { useState } from "react";
import { labelOf, money, pct, vocab } from "@/lib/tax.mjs";
import { Estimate, card, post, secondary } from "@/components/tax/common";

export default function AccountTaxPanel({ envelope }) {
  const [msg, setMsg] = useState(null);
  const panel = envelope?.panel;
  if (!panel || typeof panel !== "object") return null;
  const v = vocab(envelope);
  const r = panel.rates || {};
  const lb = panel.loss_bank || {};
  const t = lb.totals || {};
  const windows = Array.isArray(panel.wash_windows) ? panel.wash_windows : [];
  const run = panel.last_harvest_run;
  const cands = Array.isArray(panel.last_harvest_candidates) ? panel.last_harvest_candidates : [];
  const canScan = envelope?.permissions?.can_write === true && Array.isArray(envelope?.actions)
    && envelope.actions.includes("scan_account");
  async function scan() {
    setMsg(null);
    try { const d = await post(`harvest/accounts/${panel.account.id}/scan`); setMsg(d.scan?.reason || "Scanned."); }
    catch (e) { setMsg(e.message); }
  }
  return (
    <div className={`${card} p-6 text-sm`}>
      <h2 className="text-lg font-semibold text-navy">Tax</h2>
      <p className="mt-1">{labelOf(v.tax_classes, panel.account?.tax_class)} · harvesting: {panel.harvesting_engine || "—"}</p>
      {r.estimated ? (
        <p className="mt-1" title={`as of ${r.as_of}`}>Profile {r.tax_year}: {labelOf(v.filing_statuses, r.filing_status)} · combined short-term {pct(r.st, 3)}, long-term {pct(r.lt, 3)} (estimate, as of {r.as_of})</p>
      ) : <p className="mt-1 text-text-muted">Tax values: {r.label || "not estimated — no tax profile"}</p>}
      <p className="mt-2">Loss bank {lb.tax_year}: short-term gains {money(t.st_gains)}, losses {money(t.st_losses)} · long-term gains {money(t.lt_gains)}, losses {money(t.lt_losses)} · carried out ST {money(lb.carryforward_out?.st)}, LT {money(lb.carryforward_out?.lt)}</p>
      <p>Harvested value: <Estimate value={lb.tax_value?.estimated ? lb.tax_value.harvested_value : null} note={lb.tax_value?.label} rates={lb.tax_value?.rates} /></p>
      <h3 className="mt-3 font-medium text-navy">Open wash-sale windows (household)</h3>
      {windows.length ? <ul className="mt-1">{windows.map((w, i) => (
        <li key={i}>{w.kind === "loss_sale" ? "Loss sale" : "Buy"} of {w.label} in {w.account} — until {w.window_ends} (clear from {w.clear_from})</li>
      ))}</ul> : <p className="text-text-muted">None.</p>}
      <h3 className="mt-3 font-medium text-navy">Last harvest scan</h3>
      {run ? (
        <div>
          <p>{run.run_date} — {labelOf(v.run_outcomes, run.outcome)}: {run.reason}
            {run.proposal_id ? <a className="ml-2 text-navy underline" href={`/portfolio/proposals/${run.proposal_id}`}>Proposal</a> : null}</p>
          <ul className="mt-1">{cands.map((c) => (
            <li key={c.id}>{c.security || c.global_security_id}: {c.quantity} sh, {money(c.unrealized)} — <Estimate value={c.est_tax_value} note={c.value_note} />
              {c.outcome === "skipped" ? ` · ${labelOf(v.skip_reasons, c.skip_reason)}` : ` · proposed${c.replacement ? ` → ${c.replacement}` : ""}`}</li>
          ))}</ul>
        </div>
      ) : <p className="text-text-muted">Not scanned yet.</p>}
      {canScan ? <button type="button" className={`${secondary} mt-3`} onClick={scan}>Scan now</button> : null}
      {msg ? <p className="mt-2 text-text-muted">{msg}</p> : null}
      <p className="mt-3 text-xs text-text-muted">After-tax performance: {lb.after_tax_performance || "not estimated — needs prices"}</p>
    </div>
  );
}
