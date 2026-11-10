// CASH08 — the pure logic behind Household → Treasury → Payees and Invoices. Everything is decided HERE from the
// server's reads (routers/cash_payees.py → services/cash_payees, services/cash_invoices) and FAILS CLOSED:
//   * a screen renders only when the read carries `permissions.can_read === true` and a household;
//   * a write control renders only when the read says `permissions.can_write === true` (manage_payees) — or, for
//     Approve / Reject / Approve bank change, only when the server lists that action on the payee (the S30 floor: a
//     maker / the requester never sees it) — with NO truthy fallback: a lost envelope shows nothing to click;
//   * a MEMBER's read (`member_view`) is read-only: name, kind, last four and status — nothing else, no control;
//   * bank details are the LAST FOUR only ("····1234") and the custodian's reference — never a longer number;
//   * labels (kinds, statuses, flags, actions, the cash.third_party disclosure) come from the read.

const listOf = (x) => (Array.isArray(x) ? x : []);
const obj = (x) => (x && typeof x === "object" && !Array.isArray(x) ? x : {});

export function readable(data) {
  const d = obj(data);
  return obj(d.permissions).can_read === true && !!obj(d.household).id;
}

export function isMember(data) {
  return obj(data).member_view === true;
}

/** manage_payees — only an explicit `true` from the server, never for a member. */
export function canWrite(data) {
  return readable(data) && !isMember(data) && obj(obj(data).permissions).can_write === true;
}

export function disclosureText(data) {
  const l = obj(obj(obj(data).labels)["cash.third_party"]);
  return typeof l.text === "string" && l.text ? l.text : null;
}

export function vocab(data, key) {
  return listOf(obj(obj(data).vocabularies)[key]);
}

/** "1234" → "····1234"; anything that is not exactly four digits renders as "····" (never a longer number). */
export function lastFourText(v) {
  const s = String(v ?? "");
  return /^[0-9]{4}$/.test(s) ? `····${s}` : "····";
}

/** True when a string carries a digit run longer than four (the screens never render one; the tests scan for it). */
export function hasLongDigitRun(s) {
  return /[0-9]{5,}/.test(String(s ?? ""));
}

export function money(v) {
  if (v === null || v === undefined || v === "") return "—";
  const [whole, frac = ""] = String(v).split(".");
  const neg = whole.startsWith("-");
  const digits = neg ? whole.slice(1) : whole;
  return `${neg ? "-" : ""}$${digits.replace(/\B(?=(\d{3})+(?!\d))/g, ",")}${frac ? `.${frac.padEnd(2, "0").slice(0, 2)}` : ".00"}`;
}

const SLOA_STATE_FLAG = { sloa_missing: "sloa_missing", sloa_revoked: "sloa_revoked", sloa_expired: "sloa_expired",
  sloa_expiring_soon: "sloa_expiring_soon" };

/** The SLOA column: the server's flag label for the state, "On file" when current. */
export function sloaText(p, data) {
  const state = obj(obj(p).sloa).state;
  if (!state) return "—";
  if (state === "sloa_current") return "On file";
  const flags = vocab(data, "flags");
  const hit = flags.find((f) => f.key === SLOA_STATE_FLAG[state]);
  return hit ? hit.label : state;
}

export function payeeRows(data) {
  if (!readable(data)) return [];
  const member = isMember(data);
  return listOf(obj(data).items).map((p) => ({
    id: p.id, name: p.display_name, kind: p.kind_label || p.kind, lastFour: lastFourText(p.last_four),
    status: p.status_label || p.status,
    sloa: member ? null : sloaText(p, data),
    flags: member ? [] : listOf(p.flags).map((f) => f.label),
  }));
}

export function newOffer(data) {
  return canWrite(data) && listOf(obj(data).actions).some((a) => a && a.key === "new");
}

/** The server's own actions for one payee (empty for a member or a lost envelope). Write actions also need can_write;
 *  approve actions also need permissions.can_approve. */
export function payeeActions(data, payee) {
  if (!readable(data) || isMember(data)) return [];
  const perms = obj(obj(data).permissions);
  const approveKeys = new Set(["approve", "reject", "approve_bank_change", "reject_bank_change"]);
  return listOf(obj(payee).actions).filter((a) => a && (approveKeys.has(a.key) ? perms.can_approve === true
    : perms.can_write === true));
}

export function detailOf(data) {
  if (!readable(data)) return null;
  const p = obj(obj(data).payee);
  if (!p.id) return null;
  return p;
}

/** The new-payee body: only the server's editable fields; last four exactly four digits or refused here. */
export function payeeBody(data, form) {
  const editable = new Set(vocab(data, "editable"));
  if (!canWrite(data) || editable.size === 0) return { ok: false, error: "not allowed" };
  const f = obj(form);
  if (!f.kind || !String(f.display_name || "").trim()) return { ok: false, error: "kind and display name are required" };
  if (f.last_four && !/^[0-9]{4}$/.test(String(f.last_four))) return { ok: false, error: "last four: exactly four digits" };
  for (const [k, v] of Object.entries(f)) {
    if (k !== "contact_phone" && hasLongDigitRun(v)) return { ok: false, error: `${k}: never a full account or routing number` };
  }
  const body = {};
  for (const [k, v] of Object.entries(f)) if (editable.has(k) && v !== "" && v !== undefined) body[k] = v;
  return { ok: true, body };
}

/** The bank-change request body. */
export function bankChangeBody(data, form) {
  if (!canWrite(data)) return { ok: false, error: "not allowed" };
  const f = obj(form);
  if (!/^[0-9]{4}$/.test(String(f.last_four || ""))) return { ok: false, error: "new last four: exactly four digits" };
  if (!String(f.payee_reference || "").trim()) return { ok: false, error: "the new custodian reference is required" };
  if (!String(f.request_contact || "").trim()) return { ok: false, error: "record how the request arrived" };
  if (hasLongDigitRun(f.payee_reference) || hasLongDigitRun(f.bank_name)) {
    return { ok: false, error: "never a full account or routing number" };
  }
  const changes = {};
  for (const k of ["bank_name", "account_type", "last_four", "payee_reference"]) if (f[k]) changes[k] = f[k];
  return { ok: true, body: { changes, request_contact: f.request_contact, request_phone: f.request_phone || null,
    reason: f.reason || null } };
}

/** The call-back attestation: all three fields required; never sent without them. */
export function attestationBody(form) {
  const f = obj(form);
  const missing = ["person", "number", "called_at"].filter((k) => !String(f[k] || "").trim());
  if (missing.length) return { ok: false, error: `the call-back attestation needs: ${missing.join(", ")}` };
  return { ok: true, body: { person: f.person, number: f.number, called_at: f.called_at, note: f.note || null } };
}

// ── invoices ──
export function invoiceRows(data) {
  if (!readable(data) || isMember(data)) return { awaiting: [], recorded: [] };
  return {
    awaiting: listOf(obj(data).awaiting).map((d) => ({ id: d.document_id, name: d.original_filename, at: d.uploaded_at })),
    recorded: listOf(obj(data).recorded).map((o) => ({ id: o.obligation_id, name: o.name, amount: money(o.amount),
      due: o.due_date, payee: o.payee_name ? `${o.payee_name} (${lastFourText(o.payee_last_four)})` : "—",
      file: o.original_filename })),
  };
}

export function canRecord(data) {
  return canWrite(data) && listOf(obj(data).actions).some((a) => a && a.key === "record");
}

export function recordChoices(data) {
  if (!canRecord(data)) return { payees: [], payers: [] };
  const f = obj(obj(data).form);
  return { payees: listOf(f.payees).map((p) => ({ key: p.id, label: p.label })),
    payers: listOf(f.payers).map((p) => ({ key: p.id, label: p.name })) };
}

export function recordBody(data, form) {
  if (!canRecord(data)) return { ok: false, error: "not allowed" };
  const f = obj(form);
  const missing = ["document_id", "payee_id", "entity_id", "invoice_number", "amount", "due_date"].filter((k) => !f[k]);
  if (missing.length) return { ok: false, error: `required: ${missing.join(", ")}` };
  if (!/^[0-9]+(\.[0-9]{1,2})?$/.test(String(f.amount).replace(/,/g, ""))) return { ok: false, error: "amount: e.g. 1250.00" };
  return { ok: true, body: { document_id: f.document_id, payee_id: f.payee_id, entity_id: f.entity_id,
    invoice_number: f.invoice_number, amount: String(f.amount).replace(/,/g, ""), due_date: f.due_date,
    notes: f.notes || null } };
}
