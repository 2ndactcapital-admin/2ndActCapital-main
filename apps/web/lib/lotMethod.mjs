// LOTMETHOD — the pure logic behind the lot-method panels (household default,
// account override + the custodian's own method) and a proposal's override /
// lot picker. Every control is decided HERE from the server's envelope and
// fails CLOSED: a control shows only for `=== true` on the server's own flag
// AND an action the server listed (and an editable list the server sent).
// Labels — methods, preferences, custodian methods, sources — come from the
// envelope's vocabularies; nothing here restates them.

const isTrue = (v) => v === true;
const listOf = (x) => (Array.isArray(x) ? x : []);

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

const keyed = (list) => listOf(list).filter((x) => x && typeof x.key === "string");

export const methodOptions = (envelope) => keyed(vocab(envelope).methods);
export const preferenceOptions = (envelope) => keyed(vocab(envelope).gain_preferences);
export const custodianOptions = (envelope) => keyed(vocab(envelope).custodian_methods);
export const custodianSources = (envelope) => keyed(vocab(envelope).custodian_sources);

/** "Request a change" on a household or account lot-method panel. */
export function requestControl(envelope) {
  return isTrue(perms(envelope).can_write) && listOf(envelope?.actions).includes("request_change")
    && listOf(vocab(envelope).editable).length > 0;
}

/** Approve / Reject on a pending change — approve_proposals AND listed on the change (never its maker). */
export function changeControls(envelope, change) {
  const ap = isTrue(perms(envelope).can_approve);
  const a = listOf(change?.actions);
  return { approve: ap && a.includes("approve"), reject: ap && a.includes("reject") };
}

/** "Choose the lot method" on a proposal — manage_proposals AND the proposal's own action list. */
export function overrideControl(proposalEnvelope) {
  return isTrue(perms(proposalEnvelope).can_write) && listOf(proposalEnvelope?.actions).includes("override_lot_method");
}

/** The lot picker (lots envelope from /lot-method/proposals/{id}/lots): flag, action and "lots" editable. */
export function pickerControl(lotsEnvelope) {
  return isTrue(perms(lotsEnvelope).can_write) && listOf(lotsEnvelope?.actions).includes("override_lot_method")
    && listOf(vocab(lotsEnvelope).editable).includes("lots");
}

/** A realize_gains target is typed by the adviser — never computed. Decimal strings only. */
export function needsTarget(pref) {
  return pref === "realize_gains";
}

const decimalText = (s) => (typeof s === "string" && /^\d+(\.\d+)?$/.test(s.trim()) ? s.trim() : null);

/** The override body: method / preference / target, and for 'specific' the picked lots (quantities as strings). */
export function overrideBody(form) {
  const out = {};
  if (form.lot_method) out.lot_method = form.lot_method;
  if (form.gain_preference) out.gain_preference = form.gain_preference;
  if (needsTarget(form.gain_preference)) out.realize_gains_target = decimalText(form.realize_gains_target);
  if (form.lot_method === "specific") {
    out.lots = Object.entries(form.picks || {})
      .map(([k, q]) => {
        const [security, lot] = k.split("|");
        return { global_security_id: security, lot_id: lot, quantity: decimalText(q) };
      })
      .filter((x) => x.quantity && x.quantity !== "0");
  }
  return out;
}

export function householdChangeBody(form) {
  const out = { lot_method: form.lot_method, gain_preference: form.gain_preference || "prefer_long_term",
    reason: (form.reason || "").trim() };
  if (needsTarget(out.gain_preference)) out.realize_gains_target = decimalText(form.realize_gains_target);
  return out;
}

export function accountChangeBody(form) {
  const out = {
    lot_method_override: form.lot_method_override || null,
    gain_preference_override: form.gain_preference_override || null,
    custodian_cost_basis_method: form.custodian_cost_basis_method || "unknown",
    custodian_method_source: form.custodian_cost_basis_method && form.custodian_cost_basis_method !== "unknown"
      ? (form.custodian_method_source || null) : null,
    average_cost_security_ids: listOf(form.average_cost_security_ids),
    reason: (form.reason || "").trim(),
  };
  if (needsTarget(out.gain_preference_override)) out.realize_gains_target = decimalText(form.realize_gains_target);
  return out;
}

/** One line of text for a sale's method + each lot's reason (from the server's own strings). */
export function saleMethodText(trade, envelope) {
  if (!trade || trade.side !== "sell" || !trade.lot_method) return null;
  const v = vocab(envelope);
  const label = labelOf(listOf(v.lot_method?.line_methods ?? v.line_methods), trade.lot_method);
  return label || trade.lot_method_label || trade.lot_method;
}
