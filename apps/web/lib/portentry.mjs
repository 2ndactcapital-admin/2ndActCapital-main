// PORTENTRY1 — pure helpers for the direct-asset / private-investment / net-worth / integrated-import screens.
// Every label and option list comes from the API envelope; amounts are strings (exact Decimal) — never parseFloat.

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

/** Grouping only — the string is never turned into a float. */
export function money(value) {
  if (value === null || value === undefined || value === "") return "—";
  const s = String(value);
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

export function privatePayload(form) {
  const out = {
    kind: form.kind,
    name: (form.name || "").trim(),
    commitment: { amount: (form.amount || "").trim(), date: form.commitment_date },
  };
  if (form.vintage_year) out.commitment.vintage_year = Number.parseInt(form.vintage_year, 10);
  for (const k of ["taxonomy_key", "called_to_date", "distributed_to_date"]) {
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
