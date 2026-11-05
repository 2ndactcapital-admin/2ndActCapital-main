// PORTENTRY1 — pure helpers for the direct-asset / private-investment / net-worth / integrated-import screens.
// Every label and option list comes from the API envelope; amounts are strings (exact Decimal) — never parseFloat.

import { toFixedPoint } from "./fixedPoint.mjs";

const UUID = "[0-9a-fA-F-]{36}";
const ALLOWED = [
  ["GET", new RegExp(`^entities/${UUID}/(direct-assets|private-investments)$`)],
  ["POST", new RegExp(`^entities/${UUID}/(direct-assets|private-investments)$`)],
  ["POST", new RegExp(`^direct-assets/${UUID}/valuations$`)],
  ["POST", new RegExp(`^private-investments/${UUID}/(flows|nav)$`)],
  ["POST", new RegExp(`^entities/${UUID}/net-worth/recalculate$`)],
  ["PUT", new RegExp(`^entities/${UUID}/contact$`)],
  ["GET", new RegExp(`^households/${UUID}/assets$`)],
  ["GET", /^portfolio\/manual-assets$/],
  ["GET", /^portfolio\/option-contracts$/], // BUGFIX3 — the listed_option contract picker
  ["POST", /^portfolio\/manual-assets\/relink$/],
  ["GET", /^admin\/integrated-imports$/],
  ["POST", /^admin\/integrated-imports$/],
  ["GET", new RegExp(`^admin/integrated-imports/${UUID}$`)],
  ["POST", new RegExp(`^admin/integrated-imports/${UUID}/(approve|reject)$`)],
];

/** The Next proxy forwards ONLY these method + path pairs. */
export function portentryAllowed(method, path) {
  return ALLOWED.some(([m, rx]) => m === method && rx.test(String(path || "")));
}

/** Write controls render only for can_write === true AND the key in vocabularies.editable (no truthy fallback). */
export function canDo(envelope, key) {
  if (envelope?.permissions?.can_write !== true) return false;
  const ed = envelope?.vocabularies?.editable;
  return Array.isArray(ed) && ed.includes(key);
}

export function options(envelope, key) {
  const v = envelope?.vocabularies?.[key];
  return Array.isArray(v) ? v : [];
}

/** Grouping only — the string is never turned into a float. BUGFIX3: an exponent string is expanded first. */
export function money(value) {
  if (value === null || value === undefined || value === "") return "—";
  const s = toFixedPoint(value);
  const [i, f] = s.split(".");
  const neg = i.startsWith("-");
  const digits = (neg ? i.slice(1) : i).replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return `${neg ? "-" : ""}$${digits}${f ? `.${f.padEnd(2, "0").slice(0, 2)}` : ""}`;
}

/** Strip blanks; keep amounts as the typed strings. */
export function directAssetPayload(form) {
  const out = {
    kind: form.kind,
    name: (form.name || "").trim(),
    valuation: { value: (form.value || "").trim(), date: form.valuation_date, method: form.method },
  };
  for (const k of ["taxonomy_key", "sub_class", "ownership_pct", "cost_basis", "notes"]) {
    if (form[k] && String(form[k]).trim()) out[k] = String(form[k]).trim();
  }
  if (form.source && form.source.trim()) out.valuation.source = form.source.trim();
  return out;
}

/**
 * BUGFIX3 — the kind's commitment rule from the envelope (vocabularies.commitment_rules): "required" (fund),
 * "optional" (SPV) or "none" (hedge fund, direct deal, SAFE, convertible note). Unknown kind / lost envelope →
 * "required" (the form asks for MORE, never silently less; the API decides either way).
 */
export function commitmentRule(envelope, kind) {
  const rules = envelope?.vocabularies?.commitment_rules;
  const hit = Array.isArray(rules) ? rules.find((r) => r.value === kind) : null;
  return hit?.rule || "required";
}

export function privatePayload(form, rule = "required") {
  const out = { kind: form.kind, name: (form.name || "").trim() };
  const amount = (form.amount || "").trim();
  // a commitment is sent only where the kind has one, and for an SPV only when one was typed — never a zero
  if (rule === "required" || (rule === "optional" && amount)) {
    out.commitment = { amount, date: form.commitment_date };
    if (form.vintage_year) out.commitment.vintage_year = Number.parseInt(form.vintage_year, 10);
    for (const k of ["called_to_date", "distributed_to_date"]) {
      if (form[k] && String(form[k]).trim()) out[k] = String(form[k]).trim();
    }
  } else if (form.amount_invested && String(form.amount_invested).trim()) {
    out.amount_invested = String(form.amount_invested).trim();
  }
  for (const k of ["taxonomy_key", "notes"]) {
    if (form[k] && String(form[k]).trim()) out[k] = String(form[k]).trim();
  }
  if (form.nav && String(form.nav).trim()) out.nav = { value: String(form.nav).trim(), date: form.nav_date };
  return out;
}

/** Net worth's as-of line: the holdings snapshot date, or that no rollup has run. */
export function asOfText(netWorth) {
  const d = netWorth?.holdings_as_of;
  return d ? `Holdings as of ${d}` : "Holdings not yet rolled up";
}

/** Integrated import: approve / reject only from the server's own actions list (never the uploader). */
export function importActions(imp) {
  return Array.isArray(imp?.actions) ? imp.actions : [];
}
