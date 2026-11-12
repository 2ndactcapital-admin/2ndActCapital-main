/**
 * CustodianFeedPanel (ALTREAD1) — what the CUSTODIAN reports for this account: its balance as of its own date, its tax
 * lots from the cost-basis feed, and the reconciliation against Hollisworks' lots (differences highlighted). READ
 * ONLY: the lot source is changed through the lot-method change (its own gate and a second person's approval). Every
 * line renders from lib/custodianFeed.mjs and fails closed.
 */

import { balanceLines, canShow, lotRows, lotSourceText, reconRows } from "@/lib/custodianFeed.mjs";

const card = "rounded-[6px] border border-[#ece8dd] bg-white";
const differs = "bg-[#FAF9F6] text-[#9B2335]";

export default function CustodianFeedPanel({ envelope }) {
  if (!canShow(envelope)) return null;
  const balances = balanceLines(envelope);
  const lots = lotRows(envelope);
  const recon = reconRows(envelope);
  return (
    <div className={`${card} p-6`} data-testid="custodian-feed">
      <h2 className="text-lg font-semibold text-navy">Custodian</h2>
      {balances.length ? balances.map((b) => (
        <p key={b.key} className="mt-2 text-sm" data-testid="custodian-balance">
          <span className="text-text-muted">{b.label}</span> <span className="font-semibold">{b.amount}</span>
          {b.status ? <span className="text-xs text-text-muted"> · {b.status}</span> : null}
          {b.simulated ? <span className="ml-2 text-xs text-[#9B2335]">SIMULATED</span> : null}
        </p>)) : <p className="mt-2 text-sm text-text-muted" data-testid="custodian-balance-none">No custodian balance yet.</p>}
      <p className="mt-1 text-xs text-text-muted" data-testid="lot-source">Lots used by the tax engine: {lotSourceText(envelope)}</p>

      <h3 className="mt-4 font-semibold text-navy">Custodian lots</h3>
      {lots.length ? (
        <table className="mt-1 w-full text-left text-sm" data-testid="custodian-lots">
          <thead className="text-text-muted"><tr><th className="py-1">Security</th><th>Opened</th><th>Quantity</th>
            <th>Basis (adjusted)</th><th>Basis (unadjusted)</th><th>As of</th></tr></thead>
          <tbody>{lots.map((l) => (
            <tr key={l.id} className="border-t border-[#ece8dd]">
              <td className="py-1">{l.label}</td><td>{l.open_date || ""}</td><td>{l.quantity}</td>
              <td>{l.cost_basis_adjusted ?? ""}</td><td>{l.cost_basis_unadjusted ?? ""}</td><td>{l.as_of_date}</td>
            </tr>))}</tbody>
        </table>) : <p className="mt-1 text-sm text-text-muted" data-testid="custodian-lots-none">No custodian lots.</p>}

      <h3 className="mt-4 font-semibold text-navy">Reconciliation — Hollisworks lots vs the custodian&apos;s</h3>
      {recon.length ? (
        <table className="mt-1 w-full text-left text-sm" data-testid="lot-reconciliation">
          <thead className="text-text-muted"><tr><th className="py-1">Security</th><th>Hollisworks qty</th><th>Custodian qty</th>
            <th>Qty difference</th><th>Hollisworks basis</th><th>Custodian basis</th><th>Basis difference</th><th>Result</th></tr></thead>
          <tbody>{recon.map((r) => (
            <tr key={r.key} className={`border-t border-[#ece8dd] ${r.differs ? differs : ""}`}
              data-testid={r.differs ? "recon-differs" : "recon-match"}>
              <td className="py-1">{r.label}</td><td>{r.hollisworks?.quantity}</td><td>{r.custodian?.quantity}</td>
              <td className={r.quantity_differs ? "font-semibold" : ""}>{r.quantity_difference}</td>
              <td>{r.hollisworks?.basis ?? "unknown"}</td><td>{r.custodian?.basis ?? "unknown"}</td>
              <td className={r.basis_differs ? "font-semibold" : ""}>{r.basis_difference ?? "unknown"}</td>
              <td>{r.status_label || r.status}</td>
            </tr>))}</tbody>
        </table>) : <p className="mt-1 text-sm text-text-muted">Nothing to reconcile.</p>}
    </div>
  );
}
