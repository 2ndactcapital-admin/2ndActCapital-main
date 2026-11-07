// REP13 — the long/short extension: the pure logic behind the account's Long/short panel and the long/short
// part of a proposal. Every control is decided HERE from the server's envelope and FAILS CLOSED: a control shows
// only for `=== true` on the server's own flag AND an action the server listed. A missing, partial or malformed
// envelope shows none. Every label comes from the envelope's vocabularies; every number is shown exactly as the
// server states it (an estimate says "estimate"; a missing tracking error says "needs prices").

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

const has = (env, a) => listOf(env?.actions).includes(a);

/** The account panel's controls: settings need can_write, a non-empty editable vocabulary and the action; the
 * margin agreement's record / remove need can_write; approve / reject need can_approve — each with its action. */
export function panelControls(envelope) {
  const p = perms(envelope);
  const w = isTrue(p.can_write);
  const a = isTrue(p.can_approve);
  const editable = listOf(vocab(envelope).editable).length > 0;
  return {
    saveSettings: w && editable && has(envelope, "save_settings"),
    saveDraft: w && editable && has(envelope, "save_draft"),
    recordMargin: w && has(envelope, "record_margin_agreement"),
    removeMargin: w && has(envelope, "remove_margin_agreement"),
    approveMargin: a && has(envelope, "approve_margin_agreement"),
    rejectMargin: a && has(envelope, "reject_margin_agreement"),
  };
}

/** A long/short proposal's controls: acknowledge needs can_write, the caller being the maker, and the action;
 * routing needs can_write and the action (the manual route is offered whenever routing is). */
export function proposalControls(envelope) {
  const p = perms(envelope);
  const w = isTrue(p.can_write);
  return {
    acknowledge: w && isTrue(p.is_maker) && has(envelope, "acknowledge"),
    executeAdapter: w && has(envelope, "execute_adapter"),
    executeManual: w && has(envelope, "execute_manual"),
  };
}

/** The borrow-rate table's controls. */
export function rateControls(envelope) {
  const p = perms(envelope);
  return { org: isTrue(p.can_write) && has(envelope, "enter_org_rate"),
           platform: isTrue(p.can_enter_platform_rates) && has(envelope, "enter_platform_rate") };
}

/** Eligibility as the server states it: { eligible, pieces, missing }. Never "eligible" unless the server says so. */
export function eligibilityOf(envelope) {
  const e = envelope?.eligibility;
  if (!e || typeof e !== "object") return { eligible: false, pieces: [], missing: ["eligibility not available"] };
  return { eligible: isTrue(e.eligible), pieces: listOf(e.pieces), missing: listOf(e.missing) };
}

/** Every disclosure item must be ticked before the acknowledgement is sent. */
export function ackReady(envelope, ticked) {
  const items = listOf(envelope?.details?.acknowledgement_items).map((x) => x.key);
  if (!items.length) return false;
  const t = new Set(listOf(ticked));
  return items.every((k) => t.has(k));
}

/** The tracking-error cell exactly as stated ("x% a year (estimate)" or "needs prices — …"). */
export function teText(details) {
  const te = details?.tracking_error;
  if (!te || typeof te !== "object") return "—";
  return typeof te.text === "string" && te.text ? te.text : "—";
}

/** SELL SHORT / BUY TO COVER printed plainly — from the vocabulary, else the server's own side label. */
export function sideText(envelope, side, fallback) {
  const v = vocab(envelope);
  const l = labelOf(v.sides, side);
  return l && l !== side ? l : (fallback || side || "");
}
