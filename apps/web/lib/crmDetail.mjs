// CRMDETAIL1 — pure helpers for the CRM record's Relationships panel, Detail fields panel, Liabilities tab and
// net-worth lines. Every label, type and allowed pair comes from the API envelope (`vocabularies`); nothing here
// names a relationship type, a field or a liability kind. Write controls render only on
// `permissions.can_write === true` — no truthy fallback, so a lost envelope fails CLOSED.

export function canWrite(envelope) {
  return envelope?.permissions?.can_write === true;
}

export function editableList(envelope) {
  const e = envelope?.vocabularies?.editable;
  return Array.isArray(e) ? e : [];
}

// The (type, direction) choices offered on a record of `entityType`: outgoing when this record may be the FROM
// party, incoming when it may be the TO party. Ownership (panel === false) is never offered here.
export function relationshipOptions(vocabularies, entityType) {
  const types = Array.isArray(vocabularies?.relationship_types) ? vocabularies.relationship_types : [];
  const out = [];
  for (const t of types) {
    if (t.panel !== true) continue;
    const fromOk = t.from_types === null || (Array.isArray(t.from_types) && t.from_types.includes(entityType));
    const toOk = t.to_types === null || (Array.isArray(t.to_types) && t.to_types.includes(entityType));
    if (fromOk) out.push({ value: `${t.key}|outgoing`, key: t.key, direction: "outgoing", label: t.label,
      group: t.group, otherTypes: t.to_types });
    if (toOk && !t.symmetric) out.push({ value: `${t.key}|incoming`, key: t.key, direction: "incoming",
      label: t.reverse_label, group: t.group, otherTypes: t.from_types });
  }
  return out;
}

export function parseOption(value) {
  const [key, direction] = String(value || "").split("|");
  return key && (direction === "outgoing" || direction === "incoming") ? { key, direction } : null;
}

// Rows grouped by the envelope's own group order and labels.
export function groupRows(rows, vocabularies) {
  const groups = Array.isArray(vocabularies?.relationship_groups) ? vocabularies.relationship_groups : [];
  const list = Array.isArray(rows) ? rows : [];
  const out = groups
    .map((g) => ({ key: g.key, label: g.label, rows: list.filter((r) => r.group === g.key) }))
    .filter((g) => g.rows.length > 0);
  const known = new Set(groups.map((g) => g.key));
  const rest = list.filter((r) => !known.has(r.group));
  if (rest.length) out.push({ key: "other", label: "Other", rows: rest });
  return out;
}

export function typeLabelFrom(vocabularies, entityType) {
  return vocabularies?.entity_type_labels?.[entityType] || entityType || "";
}

// Detail fields: the form value for an input, and the JSON value to send back (decimals stay strings).
export function fieldInputValue(field) {
  const v = field?.value;
  if (v === null || v === undefined) return "";
  if (field.type === "boolean") return v ? "true" : "false";
  return String(v);
}

export function fieldPayload(fields, form) {
  const values = {};
  for (const f of Array.isArray(fields) ? fields : []) {
    if (f.editable_here !== true) continue;
    if (!(f.key in form)) continue;
    const raw = form[f.key];
    const before = fieldInputValue(f);
    if (String(raw ?? "") === before) continue;
    if (raw === "" || raw === null || raw === undefined) values[f.key] = null;
    else if (f.type === "boolean") values[f.key] = raw === true || raw === "true";
    else values[f.key] = String(raw);
  }
  return values;
}

// Money: amounts are strings from the API (exact Decimal). Grouping only — never parseFloat for arithmetic.
export function formatMoney(value, currency = "USD") {
  if (value === null || value === undefined || value === "") return "—";
  const s = String(value);
  const neg = s.startsWith("-");
  const [whole, frac = ""] = (neg ? s.slice(1) : s).split(".");
  const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  const cents = (frac + "00").slice(0, 2);
  const sym = currency === "USD" ? "$" : `${currency} `;
  return `${neg ? "−" : ""}${sym}${grouped}.${cents}`;
}

// The three net-worth lines in the envelope's order with its labels.
export function netWorthLines(netWorth, vocabularies) {
  const lines = Array.isArray(vocabularies?.net_worth_lines) ? vocabularies.net_worth_lines : [];
  if (!netWorth) return [];
  return lines.map((l) => ({ key: l.key, label: l.label, value: netWorth[l.key] ?? null,
    text: formatMoney(netWorth[l.key], netWorth.currency || "USD") }));
}

// "Minor" / "Deceased" markers for a person (flags computed by the API at today's date).
export function personMarkers(entity) {
  const out = [];
  if (entity?.is_deceased === true) out.push("Deceased");
  else if (entity?.is_minor === true) out.push("Minor");
  return out;
}

// Liability form → API body (strings for money, empty → omitted / null on edit).
export function liabilityPayload(form, { editing = false } = {}) {
  const keys = ["name", "kind", "lender_entity_id", "lender_name", "original_amount", "current_balance",
    "currency_code", "as_of", "rate_type", "rate_text", "maturity", "collateral_text", "notes"];
  const body = {};
  for (const k of keys) {
    const v = form?.[k];
    if (v === undefined) continue;
    const s = typeof v === "string" ? v.trim() : v;
    if (s === "" || s === null) {
      if (editing) body[k] = null;
      continue;
    }
    body[k] = k === "original_amount" || k === "current_balance" ? String(s).replace(/,/g, "") : s;
  }
  return body;
}
