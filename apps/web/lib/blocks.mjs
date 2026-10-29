// REP9 — block trading, allocation and DVP settlement: the pure logic behind
// the screens. Every control is decided HERE from the server's envelope and
// fails CLOSED: a control shows only for `=== true` on the server's own flag
// AND (on a block) an action the server listed in `actions`. A missing,
// partial or malformed envelope shows none. Every label is the envelope's.

import { labelOf, perms, vocab } from "./execution.mjs";

export { labelOf, perms, vocab };

const isTrue = (v) => v === true;
const actionsOf = (envelope) => (Array.isArray(envelope?.actions) ? envelope.actions : []);

/** Blocks list / builder: Build only with can_write === true AND the server's "build" action. */
export function listControls(envelope) {
  const p = perms(envelope);
  return { build: isTrue(p.can_write) && actionsOf(envelope).includes("build") };
}

/** Candidates screen (selection + preview + build). */
export function builderControls(envelope) {
  const p = perms(envelope);
  return { build: isTrue(p.can_write), preview: isTrue(p.can_read) };
}

/**
 * Block detail: one flag per control. A write control needs can_write === true
 * and the action in the server's list; approve / reject need can_approve,
 * can_decide === true and never a maker; correction decisions need
 * can_approve === true and the server's "decide_correction".
 */
export function blockControls(envelope) {
  const p = perms(envelope);
  const has = (a) => actionsOf(envelope).includes(a);
  const w = isTrue(p.can_write);
  const decide = isTrue(p.can_approve) && isTrue(p.can_decide) && !isTrue(p.is_maker);
  return {
    submit: w && has("submit"),
    cancel: w && has("cancel"),
    markSent: w && has("mark_sent"),
    fill: w && has("fill"),
    allocate: w && has("allocate"),
    settlement: w && has("settlement"),
    correct: w && has("correct"),
    close: w && has("close"),
    charge: w && has("charge"),
    approve: decide && has("approve"),
    reject: decide && has("reject"),
    decideCorrection: isTrue(p.can_approve) && has("decide_correction"),
    sheet: isTrue(p.can_read) && has("sheet"),
  };
}

/** Allocation policy settings: request a version / decide one (per row, the server's own list). */
export function policyControls(envelope, policyId) {
  const p = perms(envelope);
  const per = envelope && typeof envelope.actions === "object" && !Array.isArray(envelope.actions)
    ? envelope.actions[policyId] : null;
  const list = Array.isArray(per) ? per : [];
  return {
    request: isTrue(p.can_write) && isTrue(envelope?.can_request),
    approve: isTrue(p.can_approve) && list.includes("approve"),
    reject: isTrue(p.can_approve) && list.includes("reject"),
  };
}

/** Executing accounts: add / edit only with can_write === true. */
export function executionAccountControls(envelope) {
  return { add: isTrue(perms(envelope).can_write), edit: isTrue(perms(envelope).can_write) };
}

/** The quantity an allocation shows: final once booked, else the fixed pre-trade quantity. */
export function allocationQuantity(a) {
  if (!a) return "";
  return a.final_quantity !== null && a.final_quantity !== undefined ? a.final_quantity : a.allocated_quantity;
}

/** Price guidance in words, with the reference source and as-of (never a bare number). */
export function guidanceText(b, v) {
  if (!b) return "";
  let head = labelOf(v?.price_kinds, b.price_kind || "market");
  if (b.price_kind === "limit") head = `${head} ${b.limit_price}`;
  if (b.price_kind === "range") head = `${b.range_low}–${b.range_high}`;
  const r = b.reference || {};
  if (r.price) {
    head += ` · reference ${r.price} as of ${r.as_of} (${labelOf(v?.reference_sources, r.source)}${r.provider && r.source === "security_data" ? `: ${r.provider}` : ""})`;
  } else {
    head += " · no reference price on record";
  }
  return head;
}

/** One allocation's settlement instruction in words. */
export function settlementText(a) {
  if (!a) return "";
  if (a.settlement_kind === "dvp") {
    const s = a.settlement || {};
    const bits = [`DVP to ${s.receiving_custodian_name || a.receiving_custodian}`,
      `participant ${s.receiving_participant_number || "not recorded"}`];
    if (s.receiving_agent_bank) bits.push(`agent bank ${s.receiving_agent_bank}`);
    if (s.receiving_account_masked) bits.push(`account ${s.receiving_account_masked}`);
    if (s.settlement_date) bits.push(`settles ${s.settlement_date}`);
    return bits.join(" · ");
  }
  return `Receive versus payment at ${a.receiving_custodian} (integrated custodian)`;
}

/**
 * Decimal-safe build body from the builder form. Quantities and prices go as
 * STRINGS (the API refuses a JSON float); empty → omitted.
 */
export function buildBody(form) {
  const dec = (v) => {
    if (v === null || v === undefined) return undefined;
    const s = String(v).trim().replace(/,/g, "");
    return /^\d+(\.\d+)?$/.test(s) ? s : undefined;
  };
  const body = {
    instruction_ids: Array.isArray(form?.instructionIds) ? form.instructionIds : [],
    route: "manual",
    price_kind: form?.priceKind || "market",
    if_not_filled: form?.ifNotFilled || "ask",
  };
  if (form?.executionAccountId) body.execution_account_id = form.executionAccountId;
  if (form?.executorName) body.executor_name = form.executorName;
  if (form?.executorContact) body.executor_contact = form.executorContact;
  if (form?.firmContact) body.firm_contact = form.firmContact;
  if (form?.manualReason) body.manual_reason = form.manualReason;
  if (form?.crossReason) body.cross_reason = form.crossReason;
  if (body.price_kind === "limit") body.limit_price = dec(form?.limitPrice);
  if (body.price_kind === "range") {
    body.range_low = dec(form?.rangeLow);
    body.range_high = dec(form?.rangeHigh);
  }
  if (dec(form?.referencePrice)) body.reference_price = dec(form.referencePrice);
  return body;
}
