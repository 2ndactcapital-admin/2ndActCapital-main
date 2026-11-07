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
  // BUGFIX4 — edit a saved private investment (notes / dates / vintage) and read its versions
  ["PATCH", new RegExp(`^private-investments/assets/${UUID}$`)],
  ["GET", new RegExp(`^private-investments/assets/${UUID}/history$`)],
  // CATCHALL1 — a direct asset's Edit (a new version), End (a disposal row) and History
  ["PATCH", new RegExp(`^direct-assets/${UUID}$`)],
  ["POST", new RegExp(`^direct-assets/${UUID}/end$`)],
  ["GET", new RegExp(`^direct-assets/${UUID}/history$`)],
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
  } else {
    if (form.amount_invested && String(form.amount_invested).trim()) out.amount_invested = String(form.amount_invested).trim();
    // BUGFIX4 — a simplified kind (no commitment) carries its own vintage and investment date
    if (form.vintage_year && String(form.vintage_year).trim()) out.vintage_year = Number.parseInt(form.vintage_year, 10);
    if (form.investment_date) out.investment_date = form.investment_date;
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
  if (d) return `Holdings as of ${d}`;
  // ROLLUP2 — net worth is computed live: an entity with nothing counted has no holdings, not a missing rollup
  if (netWorth && typeof netWorth === "object" && netWorth.positions_counted !== undefined) {
    return Number(netWorth.unvalued_positions) ? "No valued holdings (see the holdings without a value)" : "No holdings recorded";
  }
  return "Holdings not yet rolled up";
}

/** Integrated import: approve / reject only from the server's own actions list (never the uploader). */
export function importActions(imp) {
  return Array.isArray(imp?.actions) ? imp.actions : [];
}

/** BUGFIX4 — the PATCH body of a private-investment edit: only the fields that apply to the row's shape. */
export function privateEditPayload(form, row) {
  const out = {};
  if (form.notes !== undefined) out.notes = String(form.notes || "").trim() || null;
  if (form.vintage_year !== undefined && String(form.vintage_year).trim() !== "") out.vintage_year = Number.parseInt(form.vintage_year, 10);
  if (row?.has_commitment === false) {
    if (form.investment_date) out.investment_date = form.investment_date;
  } else if (form.commitment_date) {
    out.commitment_date = form.commitment_date;
  }
  return out;
}



// ── CATCHALL1 — a direct asset's Edit / End / History ───────────────────────────────────────────────────────────
/**
 * The row's controls: each only when the envelope grants it (can_write + vocabularies.editable) AND the server
 * listed it on the row (row.actions) — a member's envelope (can_write false, editable []) renders none. A lost
 * envelope / missing actions → none (fail closed).
 */
export function directRowControls(envelope, row) {
  const acts = Array.isArray(row?.actions) ? row.actions : [];
  return {
    edit: canDo(envelope, "edit") && acts.includes("edit"),
    end: canDo(envelope, "end") && acts.includes("end"),
    history: envelope?.permissions?.can_write === true && acts.includes("history"),
    revalue: canDo(envelope, "revalue") && acts.includes("revalue"),
  };
}

/** The form's starting values for an Edit, from the row as listed. */
export function directEditForm(row) {
  return { name: row?.name || "", kind: row?.kind || "", notes: row?.notes || "", value: row?.asset_value || "",
    as_of: row?.as_of || "", method: row?.valuation_method || "" };
}

/** Only what changed from the row (a sparse PATCH); value stays a string (exact Decimal). */
export function directEditPayload(form, row) {
  const start = directEditForm(row);
  const out = {};
  for (const k of ["name", "kind", "notes", "value", "as_of", "method"]) {
    const v = form?.[k] === undefined ? start[k] : String(form[k] ?? "").trim();
    if (v !== String(start[k] ?? "").trim()) out[k] = k === "notes" ? (v || null) : v;
  }
  return out;
}

/** The End request: "entered in error" sends no date and no proceeds (the API ends it from its start). */
export function directEndPayload(form) {
  const reason = form?.end_reason || "";
  const out = { reason };
  if (reason !== "entered_in_error") {
    if (form?.end_date) out.end_date = form.end_date;
    if (form?.proceeds && String(form.proceeds).trim()) out.proceeds = String(form.proceeds).trim();
  }
  if (form?.end_notes && String(form.end_notes).trim()) out.notes = String(form.end_notes).trim();
  return out;
}
