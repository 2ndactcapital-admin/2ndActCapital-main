// REP8 — Replication Studio, the options sleeve: the pure logic behind the
// Options tab, the payoff lab, the quotes entry and the proposal's tax-gate
// panel. Every write control is decided HERE from the server's envelope and
// fails CLOSED: only `=== true` on the server's own flag AND (where the server
// lists them) an action the server named shows a control. Every label comes
// from the envelope's vocabularies; nothing here holds a list of leg roles,
// gate rules, outcomes or quote kinds of its own.

const isTrue = (v) => v === true;

export function perms(envelope) {
  const p = envelope && typeof envelope === "object" ? envelope.permissions : null;
  return p && typeof p === "object" ? p : {};
}

export function vocab(envelope) {
  const v = envelope && typeof envelope === "object" ? envelope.vocabularies : null;
  return v && typeof v === "object" ? v : {};
}

export function labelOf(list, key) {
  if (!Array.isArray(list)) return key ?? "";
  const hit = list.find((x) => x && x.key === key);
  return hit ? hit.label : (key ?? "");
}

const listed = (detail, action) => Array.isArray(detail?.actions) && detail.actions.includes(action);
const nonEmpty = (list) => Array.isArray(list) && list.length > 0;

/** Options tab: Build (manage_models + manage_replication), Enter quote, and the current package's controls. */
export function optionsControls(envelope) {
  const p = perms(envelope);
  const v = vocab(envelope);
  const detail = envelope?.current || null;
  const canBuild = isTrue(p.can_build) && nonEmpty(v.editable) && isTrue(envelope?.is_options_class)
    && !nonEmpty(envelope?.packages);
  const canWrite = isTrue(p.can_write);
  const canApprove = isTrue(p.can_approve);
  return {
    build: canBuild,
    enterQuote: canWrite && nonEmpty(v.quote_editable),
    price: canWrite && nonEmpty(v.price_editable) && listed(detail, "price"),
    submit: canWrite && listed(detail, "submit"),
    approve: canApprove && listed(detail, "approve"),
    reject: canApprove && listed(detail, "reject"),
    ...proposeControls(envelope),
  };
}

/** Raise an options proposal for an account following the model: open the approved package's legs, or roll
 *  (only when the approved package rolls an earlier one). manage_proposals; never on a draft. */
export function proposeControls(envelope) {
  const p = perms(envelope);
  const pkg = envelope?.current?.package || null;
  const ok = isTrue(p.can_propose) && pkg != null && pkg.status === "approved"
    && nonEmpty(envelope?.accounts_following);
  return { propose: ok, roll: ok && Boolean(pkg.rolled_from_id) };
}

/** A proposal's tax-gate panel: re-run only with manage_proposals; approval hidden while any rule blocks. */
export function gateControls(gateEnvelope) {
  const p = perms(gateEnvelope);
  return { rerun: isTrue(p.can_propose) };
}

export function gateBlocks(gateEnvelope) {
  if (!gateEnvelope || !Array.isArray(gateEnvelope.results)) return true;      // unknown → treat as blocking
  return gateEnvelope.results.some((r) => r && r.outcome === "block");
}

/** Approve on an options proposal: the proposal's own control AND a gate that ran and blocks nothing. */
export function optionsApproveVisible(proposalControl, gateEnvelope) {
  return isTrue(proposalControl) && gateEnvelope != null && !gateBlocks(gateEnvelope);
}

export function gateRows(gateEnvelope) {
  const v = vocab(gateEnvelope);
  const results = Array.isArray(gateEnvelope?.results) ? gateEnvelope.results : [];
  return results.map((r, i) => ({
    key: r.id || `${r.rule}-${r.trade_id || "p"}-${i}`,
    rule: labelOf(v.gate_rules, r.rule),
    outcome: labelOf(v.gate_outcomes, r.outcome),
    role: r.leg_role ? labelOf(v.leg_roles, r.leg_role) : "Whole proposal",
    reason: r.reason,
    pendingCounsel: r.pending_counsel === true,
    tone: r.outcome === "block" ? "text-[#9B2335]" : r.outcome === "flag" ? "text-navy" : "text-text-muted",
  }));
}

export function pct(v) {
  if (v === null || v === undefined || v === "") return "—";
  const n = Number(v);
  if (!Number.isFinite(n)) return "—";
  return `${(n * 100).toFixed(2).replace(/\.?0+$/, "")}%`;
}

export function legText(leg, envelope) {
  const v = vocab(envelope);
  if (!leg) return "";
  if (leg.role === "underlying") return labelOf(v.leg_roles, leg.role);
  const strike = leg.strike_level ? ` @ ${leg.strike_level}` : "";
  return `${labelOf(v.leg_roles, leg.role)} — ${pct(leg.strike_pct)} of start${strike}, ×${leg.ratio}, expires ${leg.expiry || "—"}`
    + (leg.is_flex ? " (FLEX)" : "");
}

/** The minimum account in words — exact from the quote, or "not priced". */
export function minimumText(min) {
  if (!min || typeof min !== "object") return "—";
  if (min.status === "not_priced") return min.message || "not priced — enter an index level";
  if (min.status !== "priced") return min.message || "—";
  const s = min.standard || {};
  return `$${s.minimum_account_cents} (${min.label}; ${min.multiplier} × level ${min.level} as of `
    + `${String(min.quote?.as_of || "").slice(0, 16).replace("T", " ")} UTC, ±${pct(min.tolerance)} hedge tolerance); `
    + `every account from $${s.continuous_from_cents} fits`;
}

export function quoteLine(q, envelope) {
  if (!q) return "";
  return `${labelOf(vocab(envelope).quote_kinds, q.kind)} ${q.value} — ${q.source}, as of `
    + `${String(q.as_of).slice(0, 16).replace("T", " ")} UTC (${q.age})`;
}

/** Payoff chart geometry: points → SVG paths in a fixed viewBox (no client maths on the payoff itself). */
export function chartPaths(points, { width = 560, height = 260, pad = 32 } = {}) {
  const pts = Array.isArray(points) ? points : [];
  if (!pts.length) return { replica: "", stated: "", xs: [], ys: [], zero: null };
  const xsv = pts.map((p) => Number(p.underlying_return));
  const all = pts.flatMap((p) => [Number(p.replica_return), p.stated_return == null ? null : Number(p.stated_return)])
    .filter((x) => Number.isFinite(x));
  const xmin = Math.min(...xsv), xmax = Math.max(...xsv);
  const ymin = Math.min(...all, 0), ymax = Math.max(...all, 0);
  const sx = (x) => pad + ((x - xmin) / ((xmax - xmin) || 1)) * (width - 2 * pad);
  const sy = (y) => height - pad - ((y - ymin) / ((ymax - ymin) || 1)) * (height - 2 * pad);
  const path = (key) => pts.filter((p) => p[key] != null).map((p, i) =>
    `${i ? "L" : "M"}${sx(Number(p.underlying_return)).toFixed(1)},${sy(Number(p[key])).toFixed(1)}`).join(" ");
  return { replica: path("replica_return"), stated: path("stated_return"), zero: sy(0), xmin, xmax, ymin, ymax,
    width, height, pad };
}

export function markText(mtm) {
  if (!mtm) return "—";
  if (mtm.status !== "priced") return mtm.message || "not priced — enter a quote";
  return `$${mtm.value_per_unit} per package unit (${mtm.label})`;
}

export function capRepriceText(r) {
  if (!r) return "—";
  if (r.status === "not_applicable") return r.message;
  if (r.status !== "priced") return r.message || "not priced — enter a quote";
  return `${pct(r.cap_pct)} cap at the ${r.call_strike} call (${r.label}; net put cost ${r.net_put_cost}, call premium ${r.call_premium})`;
}
