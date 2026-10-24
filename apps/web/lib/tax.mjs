// REP6 — the tax engine: the pure logic behind the Tax screens. Every control
// is decided HERE from the server's envelope and fails CLOSED: a control shows
// only for `=== true` on the server's own flag AND an action the server listed
// (on the envelope, or on the row). A missing, partial or malformed envelope
// shows none. Every label comes from the envelope's vocabularies; every tax
// number is shown as an ESTIMATE with its rates and as-of (or the server's
// reason it is not estimated).

const isTrue = (v) => v === true;

export function perms(envelope) {
  const p = envelope && typeof envelope === "object" ? envelope.permissions : null;
  return p && typeof p === "object" ? p : {};
}

export function vocab(envelope) {
  const v = envelope && typeof envelope === "object" ? envelope.vocabularies : null;
  return v && typeof v === "object" ? v : {};
}

const listOf = (x) => (Array.isArray(x) ? x : []);

export function labelOf(list, key) {
  if (!Array.isArray(list)) return key ?? "";
  const hit = list.find((x) => x && x.key === key);
  return hit ? hit.label : (key ?? "");
}

/** Page-level write controls (create / propose / seed / declare / refresh / scan). */
export function pageControls(envelope) {
  const p = perms(envelope);
  const a = listOf(envelope?.actions);
  const w = isTrue(p.can_write);
  return {
    createProfile: w && a.includes("create_profile"),
    createCarryforward: w && a.includes("create_carryforward"),
    proposeSet: w && a.includes("propose_set"),
    proposeDenial: w && a.includes("propose_denial"),
    seedDenials: w && a.includes("seed_denials"),
    declare: w && a.includes("declare"),
    refreshLossBank: w && a.includes("refresh_loss_bank"),
    scanAccount: w && a.includes("scan_account"),
  };
}

/** One row's controls: Approve / Reject / Retire need can_approve, Withdraw
 * needs can_write — each ALSO listed in the row's own server `actions`. */
export function rowControls(envelope, row) {
  const p = perms(envelope);
  const a = listOf(row?.actions);
  const ap = isTrue(p.can_approve);
  return {
    approve: ap && a.includes("approve"),
    reject: ap && a.includes("reject"),
    retire: ap && a.includes("retire"),
    withdraw: isTrue(p.can_write) && a.includes("withdraw"),
  };
}

export function money(v) {
  if (v === null || v === undefined || v === "") return "—";
  const n = Number(v);
  if (!Number.isFinite(n)) return String(v);
  return n.toLocaleString("en-US", { style: "currency", currency: "USD" });
}

export function pct(v, dp = 2) {
  if (v === null || v === undefined || v === "") return "—";
  const n = Number(v);
  if (!Number.isFinite(n)) return "—";
  return `${(n * 100).toFixed(dp)}%`;
}

/** The hover text for an estimate: the rates and their as-of. */
export function ratesTitle(rates) {
  if (!rates || rates.estimated !== true) return rates?.label || "not estimated — no tax profile";
  const parts = rates.parts || {};
  return `Estimate at the ${rates.tax_year} profile's combined rates (as of ${rates.as_of}): short-term ${pct(rates.st, 3)}`
    + ` = federal ${pct(parts.federal_ordinary, 3)} + NIIT ${pct(parts.niit, 3)} + state ${pct(parts.state, 3)}`
    + ` + city ${pct(parts.city, 3)}; long-term ${pct(rates.lt, 3)} = federal ${pct(parts.federal_lt, 3)}`
    + ` + NIIT ${pct(parts.niit, 3)} + state ${pct(parts.state_lt, 3)} + city ${pct(parts.city_lt, 3)}`;
}

/** A tax value as the screens show it: "$X (estimate)" — or the server's own
 * reason it is not estimated. Never a number without its label. */
export function estimateText(value, note) {
  if (value === null || value === undefined || value === "") return note || "not estimated";
  return `${money(value)} (estimate)`;
}

/** A trade line's wash-sale result in plain words. */
export function washText(result, v) {
  if (!result || typeof result !== "object") return "";
  const label = labelOf(v?.wash_sale_outcomes, result.outcome);
  if (result.outcome === "replaced" && result.replacement) return `${label}: ${result.replacement.label}`;
  if (result.outcome === "deferred" && result.until) return `${label} — until ${result.until}`;
  return label;
}

export function matchesOf(result) {
  // the whole line is trimmed — `.trim()` on the second literal alone ate the space before "in"
  return listOf(result?.matches).map((m) => (`${m.side === "buy" ? "buy" : "loss sale"} of ${m.label || m.security_id}`
    + ` in ${m.account} ${m.pending ? `(pending, ${m.from} to ${m.to})` : `on ${m.to}`} — ${m.identical_because || ""}`).trim());
}

/** Decimal-safe: values go to the API as STRINGS (a JSON float is refused). */
export function decimalString(v) {
  if (v === null || v === undefined) return null;
  const s = String(v).trim().replace(/,/g, "");
  if (!s) return null;
  return /^-?\d+(\.\d+)?$/.test(s) ? s : null;
}

/** Percent typed by a person ("37") → fraction string ("0.37"), exact. */
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

/** The profile body from the form: rates typed as percents → fractions. */
export function profileBody(form) {
  const b = {
    subject_type: form.subjectType, subject_id: form.subjectId, tax_year: Number.parseInt(form.taxYear, 10),
    filing_status: form.filingStatus, federal_ordinary_rate: pctToFraction(form.federalOrdinaryPct),
    federal_lt_rate: pctToFraction(form.federalLtPct), niit_applies: form.niitApplies === true,
    as_of: form.asOf, source_note: form.sourceNote,
  };
  if (form.stateCode) b.state_code = form.stateCode;
  if (form.statePct !== undefined && form.statePct !== "") b.state_rate = pctToFraction(form.statePct);
  if (form.stateLtPct !== undefined && form.stateLtPct !== "") b.state_lt_rate = pctToFraction(form.stateLtPct);
  if (form.city) b.city = form.city;
  if (form.cityPct !== undefined && form.cityPct !== "") b.city_rate = pctToFraction(form.cityPct);
  if (form.cityLtPct !== undefined && form.cityLtPct !== "") b.city_lt_rate = pctToFraction(form.cityLtPct);
  if (form.notes) b.notes = form.notes;
  return b;
}

export function subjectName(subjects, type, id) {
  const s = listOf(subjects).find((x) => x.subject_type === type && x.subject_id === id);
  return s ? (s.name || id) : id;
}
