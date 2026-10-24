// REP5 — accounts join models; rebalance proposals: the pure logic behind the
// screens. Every control is decided HERE from the server's envelope and fails
// CLOSED: a control shows only for `=== true` on the server's own flag AND an
// action the server listed in `actions`. A missing, partial or malformed
// envelope shows none. Every label comes from the envelope's vocabularies.

const isTrue = (v) => v === true;

export function perms(envelope) {
  const p = envelope && typeof envelope === "object" ? envelope.permissions : null;
  return p && typeof p === "object" ? p : {};
}

export function vocab(envelope) {
  const v = envelope && typeof envelope === "object" ? envelope.vocabularies : null;
  return v && typeof v === "object" ? v : {};
}

function actionsOf(envelope) {
  return Array.isArray(envelope?.actions) ? envelope.actions : [];
}

export function labelOf(list, key) {
  if (!Array.isArray(list)) return key ?? "";
  const hit = list.find((x) => x && x.key === key);
  return hit ? hit.label : (key ?? "");
}

/** Account → Model panel: Join / Change model / Settings / End / Generate / Approve / Reject. */
export function accountControls(envelope) {
  const p = perms(envelope);
  const a = actionsOf(envelope);
  const w = isTrue(p.can_write);
  const decide = isTrue(p.can_approve) && isTrue(p.can_decide_change);
  return {
    join: w && a.includes("join"),
    changeModel: w && a.includes("change_model"),
    changeSettings: w && a.includes("change_settings"),
    end: w && a.includes("end"),
    generate: w && a.includes("generate"),
    approveChange: decide && a.includes("approve_change"),
    rejectChange: decide && a.includes("reject_change"),
  };
}

/** Proposal detail: Submit / Approve / Reject / Execute / Refresh. */
export function proposalControls(envelope) {
  const p = perms(envelope);
  const a = actionsOf(envelope);
  const w = isTrue(p.can_write);
  const decide = isTrue(p.can_approve) && isTrue(p.can_decide) && !isTrue(p.is_maker);
  return {
    submit: w && a.includes("submit"),
    execute: w && a.includes("execute"),
    refresh: w && a.includes("refresh_execution"),
    approve: decide && a.includes("approve"),
    reject: decide && a.includes("reject"),
  };
}

/** The harvesting-engine choices to offer: ONLY the ones the server returned
 * (the custodian option exists only when its capability is evidenced 'yes'). */
export function engineOptions(envelope) {
  const list = Array.isArray(envelope?.harvesting_engines) ? envelope.harvesting_engines : [];
  return list.filter((o) => o && typeof o.key === "string");
}

export function pctText(v, dp = 2) {
  if (v === null || v === undefined || v === "") return "—";
  const n = Number(v);
  if (!Number.isFinite(n)) return "—";
  return `${(n * 100).toFixed(dp)}%`;
}

export function moneyText(v) {
  if (v === null || v === undefined || v === "") return "—";
  const n = Number(v);
  if (!Number.isFinite(n)) return String(v);
  return n.toLocaleString("en-US", { style: "currency", currency: "USD" });
}

export function amountText(t) {
  if (!t) return "";
  if (t.quantity !== null && t.quantity !== undefined) return `${t.quantity} sh`;
  if (t.notional !== null && t.notional !== undefined) return `${moneyText(t.notional)} (by amount)`;
  return "";
}

export function securityText(t) {
  const s = t?.security;
  if (!s) return t?.global_security_id || "";
  return s.ticker ? `${s.name} (${s.ticker})` : s.name || "";
}

/** Before / target / after rows joined by key, for the weights table. */
export function weightRows(proposal) {
  const map = new Map();
  const put = (list, col) => (Array.isArray(list) ? list : []).forEach((w) => {
    const r = map.get(w.key) || { key: w.key, label: w.label };
    r[col] = w.weight;
    map.set(w.key, r);
  });
  put(proposal?.before_weights, "before");
  put(proposal?.target_weights, "target");
  put(proposal?.after_weights, "after");
  return [...map.values()].sort((a, b) => (a.key === "cash") - (b.key === "cash")
    || Number(b.target || 0) - Number(a.target || 0));
}

/** Decimal-safe: values go to the API as STRINGS (a JSON float is refused). */
export function decimalString(v) {
  if (v === null || v === undefined) return null;
  const s = String(v).trim().replace(/,/g, "");
  if (!s) return null;
  return /^-?\d+(\.\d+)?$/.test(s) ? s : null;
}

/** Percent typed by a person ("2.5") → fraction string ("0.025"), exact. */
export function pctToFraction(v) {
  const s = decimalString(v);
  if (s === null) return null;
  const neg = s.startsWith("-");
  const [i, f = ""] = (neg ? s.slice(1) : s).split(".");
  const digits = (i + f).replace(/^0+(?=\d)/, "");
  const scale = f.length + 2;
  const padded = digits.padStart(scale + 1, "0");
  const out = `${padded.slice(0, padded.length - scale)}.${padded.slice(padded.length - scale)}`
    .replace(/^0+(?=\d)/, "").replace(/\.?0+$/, "");
  return `${neg ? "-" : ""}${out || "0"}`;
}

/** The settings body for join / change from the form (only what is filled). */
export function settingsBody(form) {
  const s = {};
  if (form.cashFloorPct !== undefined && form.cashFloorPct !== "") s.cash_floor = pctToFraction(form.cashFloorPct);
  if (form.driftPct !== undefined) s.drift_threshold = form.driftPct === "" ? null : pctToFraction(form.driftPct);
  if (form.gainBudget !== undefined) s.gain_budget = form.gainBudget === "" ? null : decimalString(form.gainBudget);
  if (form.exclusionPolicy) s.exclusion_policy = form.exclusionPolicy;
  if (form.frequency !== undefined) s.rebalance_frequency = form.frequency || null;
  if (typeof form.avoidShortTerm === "boolean") s.avoid_short_term = form.avoidShortTerm;
  if (form.waitDays !== undefined && form.waitDays !== "") s.short_term_wait_days = Number.parseInt(form.waitDays, 10);
  if (form.engine) s.harvesting_engine = form.engine;
  if (Array.isArray(form.exclusions)) {
    s.exclusions = form.exclusions.filter((e) => e && e.global_security_id && e.reason)
      .map((e) => ({ global_security_id: e.global_security_id, reason: e.reason }));
  }
  return s;
}
