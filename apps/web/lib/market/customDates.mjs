/**
 * "My dates": adding and deleting the caller's own dates (mkt04c). Pure: no
 * React, no DOM, no fetch — each step returns the next state and, when
 * something must be sent, the request to send.
 *
 * Every rule about a date belongs to the server. The Save button needs only a
 * non-empty trimmed name and a date; a bad name, a date outside the data, a
 * duplicate (409) or the limit come back as the server's own message, shown
 * verbatim while the form keeps what the user typed. Nothing is optimistic:
 * the list changes only when the server's response says so.
 *
 * Identity never travels here: the body is {name, event_date}, the delete URL
 * carries only the row id the server gave us.
 */

export const CUSTOM_DATES_ROUTE = "/api/market/key-dates/custom";
export const SAVE_FAILED = "The date could not be saved.";
export const DELETE_FAILED = "The date could not be deleted.";

function isObject(v) {
  return v !== null && typeof v === "object" && !Array.isArray(v);
}

export function customDateUrl(id) {
  return `${CUSTOM_DATES_ROUTE}/${encodeURIComponent(String(id))}`;
}

/**
 * The server's refusal text, verbatim. The personal endpoints repeat their
 * message as the one field error ({message: m, errors: [{msg: m}]}); a field
 * message equal to the headline is shown once, a different one is appended.
 */
export function refusalMessage(body, fallback) {
  if (isObject(body)) {
    if (typeof body.error === "string" && body.error) return body.error;
    const d = body.detail;
    if (typeof d === "string" && d) return d;
    if (isObject(d)) {
      const head = typeof d.message === "string" ? d.message : "";
      const msgs = (Array.isArray(d.errors) ? d.errors : [])
        .map((e) => (isObject(e) ? e.msg : null))
        .filter((m) => typeof m === "string" && m && m !== head);
      if (head && msgs.length) return `${head}: ${msgs.join("; ")}`;
      if (head) return head;
      if (msgs.length) return msgs.join("; ");
    }
    if (Array.isArray(d)) {
      const msgs = d.map((e) => (isObject(e) ? e.msg : null)).filter((m) => typeof m === "string" && m);
      if (msgs.length) return msgs.join("; ");
    }
  }
  return fallback;
}

/** Write controls exist only for a ready key-dates state whose envelope says can_write === true. */
export function canWriteDates(kd) {
  return kd?.kind === "ready" && kd.permissions?.can_write === true;
}

/** The server's name limit for the Name input, or undefined (no attribute) when not published. */
export function nameMaxLength(limits) {
  const n = limits?.name_max;
  return Number.isInteger(n) && n > 0 ? n : undefined;
}

// ── Add form ────────────────────────────────────────────────────────────────

export const CLOSED_FORM = Object.freeze({ open: false, name: "", date: "", saving: false, error: null });

export function openForm() {
  return { ...CLOSED_FORM, open: true };
}

export function closeForm() {
  return { ...CLOSED_FORM };
}

/** Typing keeps any server message until the next save. */
export function editForm(form, field, value) {
  if (field !== "name" && field !== "date") return form;
  return { ...form, [field]: typeof value === "string" ? value : "" };
}

/** Save needs a non-empty trimmed name and a date; every other rule is the server's. */
export function canSave(form) {
  return form.open === true && !form.saving && form.name.trim() !== "" && form.date !== "";
}

/** {form (saving), request: {url, method, body}} or null when Save is disabled. */
export function submitForm(form) {
  if (!canSave(form)) return null;
  return {
    form: { ...form, saving: true, error: null },
    request: { url: CUSTOM_DATES_ROUTE, method: "POST", body: { name: form.name, event_date: form.date } },
  };
}

/**
 * The POST result {ok, status, body} -> {form, created}. Success closes the
 * form and hands back the server's row; a refusal keeps the inputs and shows
 * the server's message.
 */
export function afterSubmit(form, { ok, body }) {
  if (ok && isObject(body) && typeof body.id === "string" && typeof body.event_date === "string") {
    return { form: closeForm(), created: body };
  }
  return { form: { ...form, saving: false, error: refusalMessage(body, SAVE_FAILED) }, created: null };
}

// ── Delete (two steps) ──────────────────────────────────────────────────────

export function deleteConfirmText(name) {
  return `Delete "${name}" from your dates?`;
}

/** Step one: ask. Nothing is sent. */
export function requestDelete(id) {
  return { id, busy: false, error: null };
}

/** Step two: confirm -> {pending (busy), request}. */
export function confirmDelete(pending) {
  if (!pending || pending.busy) return null;
  return { pending: { ...pending, busy: true, error: null }, request: { url: customDateUrl(pending.id), method: "DELETE" } };
}

/** The DELETE result -> {pending, deleted}. A refusal keeps the confirm row with the server's message. */
export function afterDelete(pending, { ok, body }) {
  if (ok) return { pending: null, deleted: true };
  return { pending: { ...pending, busy: false, error: refusalMessage(body, DELETE_FAILED) }, deleted: false };
}
