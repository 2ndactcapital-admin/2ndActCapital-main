// CASH01 — the pure logic behind Household → Treasury (coverage card, per-entity table, obligations calendar and
// list, the manual obligation / notice / minimum forms) and the member's read-only view. Every write control is
// decided HERE from the server's envelope and fails CLOSED: it shows only for `permissions.can_write === true` AND
// the action the server listed AND a non-empty editable list the server sent — a lost envelope renders nothing
// writable, and a member envelope (member_view) never does. Labels (source types, certainties, flags) come from
// the envelope's vocabularies; money stays the server's fixed-point TEXT (never a JS Number).

const isTrue = (v) => v === true;
const listOf = (x) => (Array.isArray(x) ? x : []);

export function perms(env) {
  const p = env && typeof env === "object" ? env.permissions : null;
  return p && typeof p === "object" ? p : {};
}

export function vocab(env) {
  const v = env && typeof env === "object" ? env.vocabularies : null;
  return v && typeof v === "object" ? v : {};
}

export function labelOf(list, key) {
  const hit = listOf(list).find((x) => x && x.key === key);
  return hit ? hit.label : (key ?? "");
}

/** Which write controls render. All false unless the server said so, explicitly. */
export function controls(env) {
  const ok = isTrue(perms(env).can_write) && env?.member_view !== true && listOf(vocab(env).editable).length > 0;
  const acts = listOf(env?.actions);
  const ed = listOf(vocab(env).editable);
  return {
    addObligation: ok && acts.includes("add_obligation") && ed.includes("obligation"),
    editObligation: ok && acts.includes("add_obligation") && ed.includes("obligation"),
    recordNotice: ok && acts.includes("record_notice") && ed.includes("notice"),
    setMinimum: ok && acts.includes("set_minimum") && ed.includes("minimum"),
    syncAlerts: ok && acts.includes("sync_alerts"),
  };
}

/** Group digits of a fixed-point money string without converting it to a Number ("1234567.50" → "1,234,567.50"). */
export function money(s) {
  if (s === null || s === undefined || s === "") return "—";
  const str = String(s);
  const neg = str.startsWith("-");
  const [i, f] = (neg ? str.slice(1) : str).split(".");
  const grouped = i.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return `${neg ? "-" : ""}$${grouped}${f !== undefined ? "." + f : ""}`;
}

export function monthsText(c) {
  if (!c || typeof c !== "object") return "—";
  if (c.months === null || c.months === undefined) return c.months_text || "no obligations in the next 12 months";
  return `${c.months} months`;
}

export function coveredText(c) {
  if (!c || typeof c !== "object" || !c.covered_through) return "—";
  return c.covered_beyond_horizon ? `Covered beyond ${c.covered_through}` : `Covered through ${c.covered_through}`;
}

export function gapText(c) {
  const g = c && c.next_gap;
  if (!g) return "No gap within the horizon";
  return `Next gap: ${g.name}, ~${money(g.amount)}, est. ${g.date}${g.payer_name ? ` (${g.payer_name})` : ""}`;
}

export function flagLabels(env, flags) {
  return listOf(flags).map((f) => labelOf(vocab(env).flags, f));
}

/** The obligations list, filtered by entity / certainty / source type (each "" = all). */
export function filterObligations(env, { entity = "", certainty = "", source = "" } = {}) {
  return listOf(env?.obligations).filter((o) => o && (!entity || o.payer_entity_id === entity)
    && (!certainty || o.certainty === certainty) && (!source || o.source_type === source));
}

/** How a line says where it came from: derived items name their source and why. */
export function originText(o) {
  if (!o) return "";
  if (o.derived) return `Derived from ${o.derived_from || "its source"} — ${o.explanation || ""}`.trim();
  return o.explanation || "Recorded";
}

export function estimateLabel(env) {
  const l = env && env.labels && env.labels["cash.coverage_estimate"];
  return l && typeof l.text === "string" && l.text ? l.text : null;
}

export function taxLabel(env) {
  const l = env && env.labels && env.labels["tax.estimate_label"];
  return l && typeof l.text === "string" && l.text ? l.text : null;
}

/** The manual obligation body (amount stays text; empty optional fields are omitted). */
export function obligationBody(f) {
  const out = {
    entity_id: f.entity_id, source_type: f.source_type, name: (f.name || "").trim(), amount: String(f.amount ?? "").trim(),
    due_date: f.due_date, certainty: f.certainty,
  };
  for (const k of ["rrule", "end_date", "notes", "currency_code"]) {
    if (f[k] !== undefined && String(f[k]).trim() !== "") out[k] = String(f[k]).trim();
  }
  if (f.source_ref_type && f.source_ref_id) {
    out.source_ref_type = f.source_ref_type;
    out.source_ref_id = f.source_ref_id;
  }
  return out;
}

export function noticeBody(f) {
  const out = { entity_id: f.entity_id, name: (f.name || "").trim(), amount: String(f.amount ?? "").trim(),
    due_date: f.due_date };
  if (f.commitment_id) out.commitment_id = f.commitment_id;
  if (f.notes && f.notes.trim()) out.notes = f.notes.trim();
  return out;
}

export const sourceOptions = (env) => listOf(vocab(env).source_types).filter(
  (s) => s && listOf(vocab(env).manual_source_types).includes(s.key));
export const certaintyOptions = (env) => listOf(vocab(env).certainties);
export const payerOptions = (env) => listOf(vocab(env).payers);
