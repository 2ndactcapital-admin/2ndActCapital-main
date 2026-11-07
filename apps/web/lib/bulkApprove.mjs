// BULKAPPROVE1 — the pure logic behind Approvals → Bulk approve and the
// "N pending — review in bulk" entry points. Every control is decided HERE
// from the server's envelope and fails CLOSED: Approve / Reject show only for
// `permissions.can_approve === true`, and an item is selectable only when the
// server listed a decision for it in its own `actions`. Labels, filter
// vocabularies, the blocked reasons and the per-request cap come from the
// envelope — nothing here restates them. Nothing here decides anything: the
// server runs each selected item through the single-approval path.

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

export function rowsOf(envelope) {
  return listOf(envelope && typeof envelope === "object" ? envelope.rows : null);
}

export function labelOf(list, key) {
  if (!Array.isArray(list)) return key ?? "";
  const hit = list.find((x) => x && x.key === key);
  return hit ? hit.label : (key ?? "");
}

/** The server's cap per request; with no envelope value nothing may be sent (fails closed). */
export function maxItems(envelope) {
  const n = envelope?.limits?.max_items;
  return Number.isInteger(n) && n > 0 ? n : 0;
}

/** May this viewer decide anything on this tab at all? */
export function canDecide(envelope) {
  return isTrue(perms(envelope).can_approve) && listOf(vocab(envelope).editable).includes("decision");
}

/** The decisions the server listed for one item (empty unless the viewer may approve). */
export function itemActions(envelope, item) {
  if (!canDecide(envelope)) return [];
  return listOf(item?.actions).filter((a) => a === "approve" || a === "reject");
}

/** Selectable = the server listed at least one decision. */
export function selectable(envelope, item) {
  return itemActions(envelope, item).length > 0;
}

/** Why an item is greyed — the server's own reason, or the permission when the envelope withholds approval. */
export function blockedReason(envelope, item) {
  if (selectable(envelope, item) && !item?.blocked) return null;
  if (item?.blocked_reason) return item.blocked_reason;
  if (!canDecide(envelope)) return labelOf(vocab(envelope).blocked, "permission");
  return null;
}

function ageOk(bucket, days) {
  if (!bucket || bucket.key === "any") return true;
  if (!Number.isInteger(days)) return false;
  if (bucket.min_days !== null && bucket.min_days !== undefined && days < bucket.min_days) return false;
  if (bucket.max_days !== null && bucket.max_days !== undefined && days > bucket.max_days) return false;
  return true;
}

/** Filter the rows: household, maker, subtype, age bucket (keys from the envelope's vocabularies). */
export function filterRows(envelope, filters = {}) {
  const v = vocab(envelope);
  const bucket = listOf(v.ages).find((a) => a && a.key === filters.age) || null;
  return rowsOf(envelope).filter((r) => (!filters.household || r.household_id === filters.household)
    && (!filters.maker || r.maker_id === filters.maker)
    && (!filters.subtype || r.subtype === filters.subtype)
    && ageOk(bucket, r.age_days));
}

/** "Select all" acts on the FILTERED set, and only on items the server lets this viewer decide. */
export function selectAll(envelope, filtered) {
  return listOf(filtered).filter((r) => selectable(envelope, r)).map((r) => r.id);
}

/** Toggle one id; a non-selectable item never enters the selection. */
export function toggle(envelope, selected, item) {
  const s = new Set(listOf(selected));
  if (!selectable(envelope, item)) return [...s];
  if (s.has(item.id)) s.delete(item.id); else s.add(item.id);
  return [...s];
}

/** Keep only ids still visible after a filter change (select all never reaches a hidden row). */
export function pruneSelection(selected, filtered) {
  const visible = new Set(listOf(filtered).map((r) => r.id));
  return listOf(selected).filter((id) => visible.has(id));
}

/**
 * Whether the action buttons are on: approve (every selected item lists approve), reject (every one lists
 * reject). Both off with nothing selected or over the server's cap.
 */
export function bulkControls(envelope, selected) {
  const ids = new Set(listOf(selected));
  const items = rowsOf(envelope).filter((r) => ids.has(r.id));
  const cap = maxItems(envelope);
  const base = canDecide(envelope) && items.length > 0 && items.length === ids.size && items.length <= cap;
  return {
    approve: base && items.every((r) => itemActions(envelope, r).includes("approve")),
    reject: base && items.every((r) => itemActions(envelope, r).includes("reject")),
    overCap: items.length > cap,
    cap,
    count: items.length,
  };
}

/**
 * CATCHALL1 — the first step's buttons say what they DO: they open the review, they do not decide. "Review N
 * selected →" (approve) / "Review N to reject →"; the final button in the confirmation is "Approve N" / "Reject N".
 */
export function reviewLabel(envelope, selected, decision) {
  const n = bulkControls(envelope, selected).count;
  return decision === "reject" ? `Review ${n} to reject →` : `Review ${n} selected →`;
}

/** CATCHALL1 — the final button's label in the confirmation: the server's decision label + the count. */
export function finalLabel(envelope, selected, decision) {
  return `${labelOf(vocab(envelope).decisions, decision)} ${confirmation(envelope, selected, decision).items.length}`;
}

/** CATCHALL1 — Escape / Cancel leaves the confirmation and KEEPS the selection (returns it unchanged). */
export function cancelConfirmation(selected) {
  return { asking: null, selected: listOf(selected) };
}

/** One confirmation: names the count and lists the items; reject needs a note. */
export function confirmation(envelope, selected, decision, note = "") {
  const ids = new Set(listOf(selected));
  const items = rowsOf(envelope).filter((r) => ids.has(r.id));
  const label = labelOf(vocab(envelope).decisions, decision);
  const noteOk = decision !== "reject" || String(note || "").trim().length > 0;
  return {
    title: `${label} ${items.length} item${items.length === 1 ? "" : "s"}?`,
    items: items.map((r) => ({ id: r.id, text: itemSummary(r) })),
    canConfirm: items.length > 0 && noteOk && (decision === "approve" ? bulkControls(envelope, selected).approve
      : bulkControls(envelope, selected).reject),
    noteRequired: decision === "reject",
  };
}

/** One line per item, from the server's own labels. */
export function itemSummary(r) {
  const where = [r?.household_name, r?.account_masked].filter(Boolean).join(" · ");
  const change = r?.from_label ? `${r.from_label} → ${r.to_label}` : (r?.to_label || "");
  return [where, change].filter(Boolean).join(" — ");
}

/** The POST body — the server re-checks everything per item. */
export function requestBody(envelope, selected, decision, note) {
  const body = { type: envelope?.type, item_ids: listOf(selected), decision };
  const n = String(note || "").trim();
  if (n) body.note = n;
  return body;
}

/** Per-item results, labelled by the server's own vocabulary. */
export function resultLines(envelope, response) {
  const rows = new Map(rowsOf(envelope).map((r) => [r.id, r]));
  return listOf(response?.results).map((x) => ({
    id: x.id, result: x.result, label: x.result_label || labelOf(vocab(envelope).results, x.result),
    reason: x.reason || null, text: rows.has(x.id) ? itemSummary(rows.get(x.id)) : x.id,
  }));
}

/** The entry-point link: shown only when more than one item is pending that this viewer may decide. */
export function pendingLink(summary, type) {
  const types = summary && typeof summary === "object" ? summary.types : null;
  if (!types || typeof types !== "object") return null;
  const keys = type ? [type] : Object.keys(types);
  const n = keys.reduce((s, k) => s + (Number.isInteger(types[k]?.decidable) ? types[k].decidable : 0), 0);
  if (n <= 1) return null;
  const tab = keys.length === 1 ? keys[0] : (keys.find((k) => (types[k]?.decidable || 0) > 0) || keys[0]);
  const href = typeof summary.link === "string" && summary.link.startsWith("/") ? summary.link : null;
  if (!href) return null;
  return { count: n, text: `${n} pending — review in bulk`, href: `${href}?type=${encodeURIComponent(tab)}` };
}
