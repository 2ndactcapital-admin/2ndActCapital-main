// CASH07 — the pure logic behind Household → Treasury → Payments (payment instructions between a family's OWN
// accounts). Everything is decided HERE from the server's reads (routers/payments.py → services/payments) and FAILS
// CLOSED:
//   * the screen renders only when the read carries `permissions.can_read === true` and a household;
//   * a write control renders only when the read says `permissions.can_write === true` (initiate_payment) — or, for
//     Approve / Return to draft, only when the server lists that action on the row (the S30 floor: a maker never sees
//     it) — with NO truthy fallback: a lost envelope shows nothing to click;
//   * a MEMBER's read (`member_view`) is read-only: no control at all, accounts by their last four only;
//   * the new-instruction form offers ONLY what the server's `options` read offers: eligible source accounts (the
//     others listed with both refusal reasons), that source's same-owner destinations, its declared rails;
//   * labels (statuses, rails, channels, actions, the cash.payments disclosure) come from the read.
// Money stays the server's fixed-point TEXT; dates are the server's ISO dates.

const listOf = (x) => (Array.isArray(x) ? x : []);
const obj = (x) => (x && typeof x === "object" && !Array.isArray(x) ? x : {});

export function readable(data) {
  const d = obj(data);
  return obj(d.permissions).can_read === true && !!obj(d.household).id;
}

export function isMember(data) {
  return obj(data).member_view === true;
}

/** initiate_payment (draft / send / reconcile …) — only an explicit `true` from the server, never for a member. */
export function canWrite(data) {
  return readable(data) && !isMember(data) && obj(obj(data).permissions).can_write === true;
}

export function disclosureText(data) {
  const l = obj(obj(obj(data).labels)["cash.payments"]);
  return typeof l.text === "string" && l.text ? l.text : null;
}

export function vocab(data, key) {
  return listOf(obj(obj(data).vocabularies)[key]);
}

export function labelOf(list, key) {
  const hit = listOf(list).find((x) => x && x.key === key);
  return hit ? hit.label : (key ?? "");
}

/** "****1234" → "····1234": the last four only, whatever the stored mask's length. */
export function lastFour(masked) {
  const s = String(masked || "");
  const tail = s.replace(/[^A-Za-z0-9]/g, "").slice(-4);
  return tail ? `····${tail}` : "····";
}

export function money(v) {
  if (v === null || v === undefined || v === "") return "—";
  const [whole, frac = ""] = String(v).split(".");
  const neg = whole.startsWith("-");
  const digits = neg ? whole.slice(1) : whole;
  return `${neg ? "-" : ""}$${digits.replace(/\B(?=(\d{3})+(?!\d))/g, ",")}${frac ? `.${frac.padEnd(2, "0").slice(0, 2)}` : ".00"}`;
}

export function accountText(a) {
  const x = obj(a);
  return [lastFour(x.account_number_masked), x.owner_name, x.custodian_label].filter(Boolean).join(" · ");
}

/** The list rows as the screen shows them. */
export function paymentRows(data) {
  if (!readable(data)) return [];
  return listOf(obj(data).items).map((p) => ({
    id: p.id, status: p.status, statusLabel: p.status_label || p.status, amount: money(p.amount), rail: p.rail_label || p.rail,
    valueDate: p.value_date, from: accountText(p.source), to: accountText(p.destination), purpose: p.purpose || "",
    provisional: p.provisional === true ? `provisional until ${p.provisional_until}` : null,
    actions: isMember(data) ? [] : listOf(p.actions).filter((a) => a && a.key && a.label),
  }));
}

/** The list's filter choices (the server's vocabularies). */
export function filterOptions(data) {
  return { statuses: [{ key: "open", label: "Open" }, ...vocab(data, "statuses")], rails: vocab(data, "rails") };
}

/** "New payment instruction" — only when the server offered it AND the caller may write. */
export function newOffer(data) {
  return canWrite(data) && listOf(obj(data).actions).some((a) => a && a.key === "new");
}

// ── the new-instruction form (from the server's `options` read) ───────────────────────────────────────────────────
export function sourceChoices(opts) {
  if (!canWrite(opts)) return { eligible: [], refused: [] };
  const all = listOf(obj(opts).sources);
  return {
    eligible: all.filter((s) => s.eligible === true).map((s) => ({ key: s.id, label: accountText(s) })),
    refused: all.filter((s) => s.eligible !== true).map((s) => ({ key: s.id, label: accountText(s), reasons: listOf(s.reasons) })),
  };
}

function sourceOf(opts, sourceId) {
  return listOf(obj(opts).sources).find((s) => s.id === sourceId && s.eligible === true) || null;
}

/** Destinations: ONLY the chosen (eligible) source's same-owner accounts. */
export function destinationChoices(opts, sourceId) {
  const s = sourceOf(opts, sourceId);
  return s ? listOf(s.destinations).map((d) => ({ key: d.id, label: accountText(d) })) : [];
}

/** Rails: ONLY what the chosen source's custodian declares. */
export function railChoices(opts, sourceId) {
  const s = sourceOf(opts, sourceId);
  return s ? listOf(s.rails) : [];
}

export function linkChoices(opts) {
  return listOf(obj(opts).links).map((l) => ({ key: `${l.type}:${l.id}`, label: l.label, type: l.type, id: l.id }));
}

/** The draft body, or the reasons it is not ready (the server re-checks everything). */
export function draftBody(opts, form) {
  const f = obj(form);
  const errors = [];
  if (!sourceOf(opts, f.source)) errors.push("choose a source account you may instruct");
  if (!destinationChoices(opts, f.source).some((d) => d.key === f.destination)) errors.push("choose a same-owner destination");
  if (!railChoices(opts, f.source).some((r) => r.key === f.rail)) errors.push("choose a rail the custodian declares");
  if (!/^\d+(\.\d{1,2})?$/.test(String(f.amount || "").trim()) || Number(f.amount) <= 0) errors.push("enter an amount (dollars and cents)");
  if (!/^\d{4}-\d{2}-\d{2}$/.test(String(f.valueDate || ""))) errors.push("choose a value date");
  if (!String(f.purpose || "").trim()) errors.push("say what the payment is for");
  if (errors.length) return { ok: false, errors };
  const link = linkChoices(opts).find((l) => l.key === f.link) || null;
  return {
    ok: true,
    body: {
      source_account_id: f.source, destination_account_id: f.destination, amount: String(f.amount).trim(),
      rail: f.rail, value_date: f.valueDate, purpose: String(f.purpose).trim(),
      ...(link ? { purpose_ref_type: link.type, purpose_ref_id: link.id } : {}),
    },
  };
}

// ── detail ────────────────────────────────────────────────────────────────────────────────────────────────────────
export function detailOf(data) {
  if (!readable(data)) return null;
  const p = obj(obj(data).payment);
  if (!p.id) return null;
  return {
    id: p.id, status: p.status_label || p.status, amount: money(p.amount), rail: p.rail_label || p.rail,
    valueDate: p.value_date, from: accountText(p.source), to: accountText(p.destination), purpose: p.purpose || "",
    link: p.purpose_ref_label || null, provisional: p.provisional === true ? p.provisional_until : null,
    timeline: listOf(p.timeline).map((e) => ({ id: e.id, label: e.label, to: e.to_label, at: e.at, who: e.who,
      note: obj(e.detail).reason || obj(e.detail).note || obj(e.detail).channel || obj(e.detail).return_code || null })),
    actions: isMember(data) ? [] : listOf(p.actions).filter((a) => a && a.key && a.label),
  };
}

/** The fields each action asks for before it is sent (what the API's ActionIn takes). */
export const ACTION_FIELDS = {
  mark_sent: ["channel", "external_ref", "note"], consent_requested: ["note"], acknowledge: ["external_ref", "note"],
  settle: ["settled_on", "note"], custodian_reject: ["reason"], return: ["return_code", "reason"],
  reconcile: ["method", "transaction_ids", "reference", "note"], close: ["note"], cancel: ["reason"],
  submit: [], approve: ["note"], reject: ["note"],
};

/** The request an action sends: [path suffix, body]. Approve / reject go to their own routes. */
export function actionRequest(key, fields = {}) {
  const f = obj(fields);
  const body = {};
  for (const k of ACTION_FIELDS[key] || []) {
    const v = f[k];
    if (v === undefined || v === null || v === "" || (Array.isArray(v) && !v.length)) continue;
    body[k] = v;
  }
  return [key, body];
}

/** Reconcile: matched feed rows (custodian_data) — or manual, which needs a reason. */
export function reconcileBody(matches, chosenIds, note) {
  const ids = listOf(chosenIds).filter((id) => listOf(obj(matches).matches).some((m) => m.id === id));
  if (ids.length) return { ok: true, body: { method: "custodian_data", transaction_ids: ids, ...(note ? { note } : {}) } };
  if (String(note || "").trim()) return { ok: true, body: { method: "manual", note: String(note).trim() } };
  return { ok: false, error: "choose the matching feed transaction(s), or confirm manually with a reason" };
}
