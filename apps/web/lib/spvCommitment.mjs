// BUGFIX1 (SPV-01) — the ONE way an SPV's committed money is worded on screen.
// The numbers come from the API's `commitment` object
// (services/spv_rollup.spv_commitment_summaries): the cap table, the SPV list
// card and the dashboard SPV card all render through this, so they cannot
// disagree. Percent of target is never capped (134% reads 134%); only the
// bar's drawn width stops at 100%.
import { formatCurrency } from "./format.js";

export function commitmentView(c) {
  if (!c) return null;
  const pct = c.pct_of_target;
  const labels = c.labels || {};
  return {
    committedText: `${formatCurrency(c.total_committed)} committed`,
    pctText: pct == null ? null : `${pct}% of ${formatCurrency(c.target_raise)} target`,
    overTarget: Boolean(c.over_target),
    overTargetText: c.over_target ? labels.over_target || null : null,
    barPct: pct == null ? null : Math.max(0, Math.min(100, pct)),
    softText: `${labels.soft || "soft"}: ${formatCurrency(c.soft_committed)}`
      + (c.soft_count ? ` (${c.soft_count})` : ""),
    hardText: `${labels.hard || "hard"}: ${formatCurrency(c.hard_committed)}`
      + (c.hard_count ? ` (${c.hard_count})` : ""),
  };
}
