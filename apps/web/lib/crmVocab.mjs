// PORTENTRY1 Task 2 — the CRM's per-org vocabularies (entity statuses, marital statuses, sub-types per entity type)
// read ONLY from the API envelope (`GET /api/v1/crm/vocabularies` → vocabularies). No label lives in the page; a lost
// envelope yields empty option lists (the form then shows the stored value only), never a client-side default list.

function list(v) {
  return Array.isArray(v) ? v : [];
}

/** Options for the Status select: the selectable statuses, plus the record's current value when it is not one
 *  (a deceased person, a legacy value) so saving the form never silently changes it. */
export function statusOptions(vocab, current) {
  const all = list(vocab?.entity_statuses);
  const opts = all.filter((s) => s.selectable).map((s) => ({ value: s.value, label: s.label }));
  if (current && !opts.some((o) => o.value === current)) {
    const known = all.find((s) => s.value === current);
    opts.push({ value: current, label: known ? known.label : current });
  }
  return opts;
}

export function statusLabel(vocab, value) {
  return list(vocab?.entity_statuses).find((s) => s.value === value)?.label || value || "";
}

/** The CRM list's status pills ("All" + every status), from the envelope. */
export function statusFilters(vocab) {
  return list(vocab?.status_filters);
}

export function maritalOptions(vocab) {
  return list(vocab?.marital_statuses).filter((s) => s.selectable !== false).map((s) => ({ value: s.value, label: s.label }));
}

export function maritalLabel(vocab, value) {
  return list(vocab?.marital_statuses).find((s) => s.value === value)?.label || value || "";
}

/** Sub-type options for one entity type (stored value = the label). The current value is kept when it is a legacy
 *  label no longer in the vocabulary. */
export function subTypeOptions(vocab, entityType, current) {
  const opts = list(vocab?.sub_types_by_type?.[entityType]).map((o) => ({ value: o.value, label: o.label }));
  if (current && opts.length && !opts.some((o) => o.value === current)) opts.push({ value: current, label: current });
  return opts;
}

export function freeTextSubType(vocab, entityType) {
  return list(vocab?.free_text_sub_type_types).includes(entityType);
}
