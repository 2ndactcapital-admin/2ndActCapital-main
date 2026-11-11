// CPSIM01 — Admin → Counterparties: pure helpers over the server's envelope. Every write control renders only when
// permissions.can_write is EXACTLY true (a lost or partial envelope fails CLOSED); labels, institution types, field
// groups, statuses, outcomes and tabs come from the server's vocabularies (Rule 1).

export const can = (env, flag) => env?.permissions?.[flag] === true;
export const canWrite = (env) => can(env, "can_write");
export const vocab = (env, name) => (Array.isArray(env?.vocabularies?.[name]) ? env.vocabularies[name] : []);
export const labelOf = (list, key) => (list || []).find((x) => x.key === key)?.label ?? key ?? "";
export const typeOf = (env, key) => vocab(env, "institution_types").find((t) => t.key === key) || null;
export const groupsOf = (env, key) => typeOf(env, key)?.field_groups || [];

export const IMPORT_EXPORT_NOTE = "A book (households, accounts, positions, transactions) belongs to a REST + OAuth " +
  "custodian type; this institution type has none";
export const SIMULATED_NOTE =
  "Simulated counterparties only. Nothing on this screen is a real custodian or bank: every key, routing number, " +
  "account, BIC and IBAN is generated so that it can never be real.";

// The tabs of one counterparty, in the server's order; a tab is enabled only when the institution type declares the
// field group behind it. cpsim02: the BOOK tabs (Import / Export, Connections, Webhooks, Conformance — the server's
// book_tabs) are enabled only for the server's book_institution_types (a REST + OAuth custodian); an unknown tab or a
// lost vocabulary stays disabled (fails closed).
export function tabsFor(env, typeKey) {
  const groups = new Set(groupsOf(env, typeKey));
  const bookTabs = new Set(Array.isArray(env?.vocabularies?.book_tabs) ? env.vocabularies.book_tabs : []);
  const bookTypes = Array.isArray(env?.vocabularies?.book_institution_types) ? env.vocabularies.book_institution_types : [];
  const hasBook = bookTypes.includes(typeKey);
  const needs = {
    connection: () => groups.has("connection"),
    fix: () => groups.has("fix"),
    bank: () => groups.has("bank_domestic") || groups.has("bank_international"),
    behaviour: () => groups.has("behaviour"),
    clock: () => groups.has("clock"),
    activity: () => true,
  };
  return vocab(env, "tabs").map((t) => ({
    ...t, enabled: bookTabs.has(t.key) ? hasBook : Boolean((needs[t.key] || (() => false))()),
  }));
}

// ── cpsim02: the book, connections, webhooks, conformance (pure helpers over the server's envelopes) ───────────────
export const bookVocab = (env) => env?.vocabularies?.book || null;
export const bookKinds = (env) => (Array.isArray(bookVocab(env)?.kinds) ? bookVocab(env).kinds : []);

// The preview's rejected rows, each with the server's reason label (Rule 1) and its detail flattened for a table cell.
export function rejectionRows(preview, env) {
  const reasons = Array.isArray(bookVocab(env)?.reasons) ? bookVocab(env).reasons : [];
  return (preview?.rejected || []).map((r) => ({
    row: r.row, id: r.id || "", reason: r.reason, label: labelOf(reasons, r.reason) || r.message || r.reason,
    detail: r.detail ? Object.entries(r.detail).map(([k, v]) => `${k}: ${Array.isArray(v) ? v.join(", ") : v}`).join("; ") : "",
  }));
}

// Accept is offered only for a preview with at least one accepted row, and only to a writer.
export const canAccept = (env, preview) => canWrite(env) && (preview?.counts?.accepted ?? 0) > 0;

export function templateHref(id, kind, format) {
  return `/api/admin/counterparties/${encodeURIComponent(id)}/book/template?kind=${encodeURIComponent(kind)}&format=${format}`;
}

export function exportHref(id, format, kind) {
  const k = kind ? `&kind=${encodeURIComponent(kind)}` : "";
  return `/api/admin/counterparties/${encodeURIComponent(id)}/book/export?format=${format}${k}`;
}

// The latest conformance run as a summary line + its failed checks (each citing its contract row).
export function conformanceSummary(run) {
  if (!run) return null;
  const results = Array.isArray(run.results) ? run.results : [];
  return {
    passed: run.passed ?? results.filter((r) => r.passed === true).length,
    failed: run.failed ?? results.filter((r) => r.passed === false).length,
    failures: results.filter((r) => r.passed === false).map((r) => ({ id: r.id, contract: r.contract, label: r.label })),
    at: run.finished_at || run.started_at || null,
  };
}

// Outbound webhook deliveries from the request log (direction 'out').
export const deliveries = (requests) => (requests?.rows || []).filter((r) => r.direction === "out");

export function liveKey(detail) {
  return (detail?.credentials || []).find((c) => c.kind === "api_key" && c.state === "live") || null;
}

export function shortDate(v) {
  return typeof v === "string" ? v.slice(0, 10) : "";
}
