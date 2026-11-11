// REP2 — execution instructions, custodian capabilities and human tasks: the
// pure logic behind the screens. Every control is decided HERE from the
// server's envelope and fails CLOSED: a control shows only for `=== true` on
// the server's own flag, or for an action the server listed in `actions`. A
// missing, partial or malformed envelope shows none. Every label comes from
// the envelope's vocabularies.

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

/** Instructions list: the New instruction button. */
export function listControls(envelope) {
  return { create: isTrue(perms(envelope).can_write) };
}

/**
 * Instruction detail: one flag per control. A write control needs BOTH the
 * envelope's flag and the action in the server's `actions` list (computed for
 * this caller and this status); Approve / Reject additionally need
 * can_decide === true and is_maker !== true.
 */
export function instructionControls(envelope) {
  const p = perms(envelope);
  const actions = Array.isArray(envelope?.actions) ? envelope.actions : [];
  const has = (a) => actions.includes(a);
  const canWrite = isTrue(p.can_write);
  const decide = isTrue(p.can_approve) && isTrue(p.can_decide) && !isTrue(p.is_maker);
  return {
    edit: canWrite && has("edit"),
    submit: canWrite && has("submit"),
    markSent: canWrite && has("mark_sent"),
    fill: canWrite && has("fill"),
    reconcile: canWrite && has("reconcile"),
    close: canWrite && has("close"),
    cancel: canWrite && has("cancel"),
    approve: decide && has("approve"),
    reject: decide && has("reject"),
    letter: isTrue(p.can_read) && has("letter"),
    // REP9 — the order execution sheet (broker / desk / trader): the manual route, always offered
    orderSheet: isTrue(p.can_read) && has("order_sheet"),
    // CPSIM03 — the custodian-API route's two PERSON actions (the server lists them only when D2 holds / an order exists)
    sendToCustodian: canWrite && has("send_to_custodian"),
    importFills: canWrite && has("import_fills"),
  };
}

// ── CPSIM03 — the custodian-API route (a SIMULATED custodian only) ─────────────────────────────────────────────────
/** The instruction's custodian panel, from the server's own `instruction.custodian` (absent → nothing simulated). */
export function custodianModel(envelope) {
  const c = envelope?.instruction?.custodian;
  if (!c || typeof c !== "object") {
    return { simulated: false, routedVia: null, orderId: null, counterparty: null, sendRefused: null };
  }
  return {
    simulated: c.simulated === true,
    routedVia: typeof c.routed_via === "string" ? c.routed_via : null,
    orderId: typeof c.custodian_order_id === "string" ? c.custodian_order_id : null,
    counterparty: typeof c.counterparty === "string" ? c.counterparty : null,
    sendRefused: typeof c.send_refused === "string" ? c.send_refused : null,
  };
}

/** "Mark sent" offers the MANUAL channels only (the custodian API channel is recorded by Send to custodian). */
export function manualChannels(v) {
  const all = Array.isArray(v?.sent_channels) ? v.sent_channels : [];
  const allowed = Array.isArray(v?.manual_sent_channels) ? new Set(v.manual_sent_channels) : null;
  return all.filter((c) => c && (allowed ? allowed.has(c.key) : c.key !== "custodian_api"));
}

/** The reconcile picker's synced transactions (the server's list) and the ids it preselects. */
export function reconcilePicker(orderView) {
  const rows = Array.isArray(orderView?.synced_transactions) ? orderView.synced_transactions : [];
  const pre = new Set(Array.isArray(orderView?.preselect_transaction_ids) ? orderView.preselect_transaction_ids : []);
  return {
    rows: rows.map((r) => ({ ...r, checked: pre.has(r.id) })),
    reference: typeof orderView?.reconcile_reference === "string" ? orderView.reconcile_reference : "",
  };
}

/** The custodian order's executions, each marked booked or not. */
export function orderExecutions(orderView) {
  const ex = orderView?.order?.executions;
  return Array.isArray(ex) ? ex.map((e) => ({ ...e, booked: e.booked === true })) : [];
}

/** Capabilities matrix: Edit only for Super Admin (the server's own flags). */
export function capabilityControls(envelope) {
  const p = perms(envelope);
  return { edit: isTrue(p.can_write) && isTrue(p.is_super_admin) };
}

export function amountText(row) {
  if (!row) return "";
  if (row.quantity !== null && row.quantity !== undefined) return `${row.quantity} sh`;
  if (row.notional !== null && row.notional !== undefined) return `${row.notional} notional`;
  return "";
}

export function priceText(row, priceKinds) {
  if (!row) return "";
  if (row.price_kind === "limit") return `${labelOf(priceKinds, "limit")} ${row.limit_price}`;
  if (row.price_kind === "range") {
    return `${row.range_low}–${row.range_high} (ref ${row.reference_price} at ${String(row.reference_at || "").slice(0, 16).replace("T", " ")})`;
  }
  return labelOf(priceKinds, "market");
}

export function securityText(row) {
  const s = row?.security;
  if (!s) return "";
  return s.ticker ? `${s.name} (${s.ticker})` : s.name || "";
}

/** "Unknown" shown plainly; never blank, never coloured as available. */
export function capabilityCell(cell, values) {
  if (!cell) return { text: labelOf(values, "unknown"), available: false };
  return { text: labelOf(values, cell.value), available: cell.available === true };
}

/**
 * Decimal-safe request body: quantities and prices go to the API as STRINGS
 * (the API refuses a JSON float). Empty → null.
 */
export function decimalString(v) {
  if (v === null || v === undefined) return null;
  const s = String(v).trim().replace(/,/g, "");
  if (!s) return null;
  return /^-?\d+(\.\d+)?$/.test(s) ? s : null;
}

/** Build the POST /execution/instructions body for ONE trade from the form. */
export function tradeListBody(form) {
  const kind = form.priceKind || "market";
  const price = { kind };
  if (kind === "limit") price.limit_price = decimalString(form.limitPrice);
  if (kind === "range") {
    price.range_low = decimalString(form.rangeLow);
    price.range_high = decimalString(form.rangeHigh);
    price.reference_price = decimalString(form.referencePrice);
    price.reference_at = form.referenceAt ? new Date(form.referenceAt).toISOString() : null;
  }
  const trade = {
    global_security_id: form.securityId,
    side: form.side,
    price_guidance: price,
    lot_instructions: (form.lots || []).filter((l) => decimalString(l.quantity))
      .map((l) => ({ tax_lot_id: l.id, quantity: decimalString(l.quantity) })),
  };
  if (form.amountMode === "notional") trade.notional = decimalString(form.amount);
  else trade.quantity = decimalString(form.amount);
  if (form.washSaleNotes) trade.wash_sale_notes = form.washSaleNotes;
  const body = {
    account_id: form.accountId,
    trades: [trade],
    executor_type: form.executorType,
    execute_by: form.executeBy,
    if_not_filled: form.ifNotFilled || "ask",
  };
  if (form.executorName) body.executor_name = form.executorName;
  if (form.executorContact) body.executor_contact = form.executorContact;
  if (form.notes) body.notes = form.notes;
  return body;
}

/** My tasks panel: the two lists, safely. */
export function myTasksModel(payload) {
  const list = (x) => (Array.isArray(x) ? x : []);
  return { actionable: list(payload?.actionable), waiting: list(payload?.waiting_on_others) };
}
