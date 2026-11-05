// BUGFIX3 — pure helpers (node-testable) for the option-contract picker and the manual-assets report.

/** "Call 150 · 2027-01-15 · ×100 (AAPL270115C00150000)" — every part from the API row. */
export function optionContractLabel(r) {
  const type = r?.option_type ? r.option_type[0].toUpperCase() + r.option_type.slice(1) : "";
  const parts = [`${type} ${r?.strike ?? ""}`.trim(), r?.expiry, r?.multiplier ? `×${r.multiplier}` : null];
  return `${parts.filter(Boolean).join(" · ")}${r?.occ_symbol ? ` (${r.occ_symbol})` : ""}`;
}

/** The picked contract's terms for the manual held-away row. */
export function contractTerms(r) {
  return { option_type: r.option_type, strike: r.strike, expiry: r.expiry, multiplier: r.multiplier };
}

/** "3 positions · 2 owners" / "not used by any position". */
export function manualAssetNote(a) {
  const n = Number.parseInt(a?.position_count ?? 0, 10) || 0;
  const o = Number.parseInt(a?.owner_count ?? 0, 10) || 0;
  if (n === 0) return "not used by any position";
  return `${n} position${n === 1 ? "" : "s"} · ${o} owner${o === 1 ? "" : "s"}`;
}

// BUGFIX3 FIX 1 — what the last rollup left out of net worth (the API's `skipped` report:
// services/portfolio_refresh.skipped_of). A count of 0, or no report at all, renders nothing; never a
// client-side guess at what was skipped.
export function skippedSentence(skipped) {
  const n = Number(skipped?.count) || 0;
  if (n <= 0) return null;
  const unc = Number(skipped?.unclassified) || 0;
  const unv = Number(skipped?.unvalued) || 0;
  const parts = [];
  if (unc > 0) parts.push(`${unc} without an asset class`);
  if (unv > 0) parts.push(`${unv} without a value`);
  const what = n === 1 ? "1 holding is" : `${n} holdings are`;
  return `${what} not counted in net worth${parts.length ? ` (${parts.join(", ")})` : ""}.`;
}
