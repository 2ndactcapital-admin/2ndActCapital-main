"use client";

/**
 * OptionsTaxGate — REP8, the options tax gate's results on an options
 * proposal: per leg (and for the whole proposal) the rule, its outcome (pass /
 * flag / block), the plain-words reason and the "pending counsel review"
 * label where the open straddle question decides it. A block refuses approval
 * on the server (409 naming the rule); the proposal view also hides Approve
 * while any rule blocks (`optionsApproveVisible`). Re-run renders only from
 * `gateControls(envelope)`. Labels from the envelope's vocabularies.
 */

import { gateControls, gateRows } from "@/lib/replicationOptions.mjs";

const card = "rounded-[6px] border border-[#ece8dd] bg-white";
const secondary = "rounded border border-navy px-3 py-1 text-navy disabled:opacity-50";

export function TaxGatePanelView({ gate, onRerun, busy }) {
  if (!gate) return <div className={`${card} p-6 text-sm text-text-muted`}>The options tax gate has not run yet.</div>;
  const rows = gateRows(gate);
  const c = gateControls(gate);
  const blocks = rows.filter((r) => r.tone === "text-[#9B2335]").length;
  return (
    <div className={`${card} p-6 text-sm`}>
      <h2 className="text-lg font-semibold text-navy">Options tax gate
        {blocks ? <span className="ml-2 rounded bg-[#FAF9F6] px-2 text-xs text-[#9B2335]">{blocks} rule(s) block approval</span> : null}</h2>
      <p className="mt-1 text-xs text-text-muted">Run {String(gate.evaluated_at || "").slice(0, 16).replace("T", " ")} UTC — every result is stored with its inputs.</p>
      <table className="mt-2 w-full">
        <caption className="sr-only">Tax-gate results per leg</caption>
        <thead className="text-left text-text-muted"><tr><th className="py-1">Leg</th><th>Rule</th><th>Outcome</th><th>Why</th></tr></thead>
        <tbody>{rows.map((r) => (
          <tr key={r.key} className="border-t border-[#ece8dd] align-top">
            <td className="py-1">{r.role}</td><td>{r.rule}</td><td className={`font-medium ${r.tone}`}>{r.outcome}</td>
            <td>{r.reason}{r.pendingCounsel && gate?.vocabularies?.pending_counsel_label ? <span className="ml-1 rounded bg-[#E8D5A3] px-1 text-xs text-navy">{gate.vocabularies.pending_counsel_label}</span> : null}</td>
          </tr>
        ))}</tbody>
      </table>
      {c.rerun && onRerun ? <button type="button" className={`${secondary} mt-3`} disabled={busy} onClick={onRerun}>Run the gate again</button> : null}
    </div>
  );
}
