// CPSIM01 — Admin → Counterparties: pure helpers over the server's envelope. Every write control renders only when
// permissions.can_write is EXACTLY true (a lost or partial envelope fails CLOSED); labels, institution types, field
// groups, statuses, outcomes and tabs come from the server's vocabularies (Rule 1).

export const can = (env, flag) => env?.permissions?.[flag] === true;
export const canWrite = (env) => can(env, "can_write");
export const vocab = (env, name) => (Array.isArray(env?.vocabularies?.[name]) ? env.vocabularies[name] : []);
export const labelOf = (list, key) => (list || []).find((x) => x.key === key)?.label ?? key ?? "";
export const typeOf = (env, key) => vocab(env, "institution_types").find((t) => t.key === key) || null;
export const groupsOf = (env, key) => typeOf(env, key)?.field_groups || [];

export const IMPORT_EXPORT_NOTE = "Arrives with the Altruist API — cpsim02";
export const SIMULATED_NOTE =
  "Simulated counterparties only. Nothing on this screen is a real custodian or bank: every key, routing number, " +
  "account, BIC and IBAN is generated so that it can never be real.";

// The tabs of one counterparty, in the server's order; a tab is enabled only when the institution type declares the
// field group behind it. Import / Export is a placeholder (disabled) until cpsim02.
export function tabsFor(env, typeKey) {
  const groups = new Set(groupsOf(env, typeKey));
  const needs = {
    connection: () => groups.has("connection"),
    fix: () => groups.has("fix"),
    bank: () => groups.has("bank_domestic") || groups.has("bank_international"),
    behaviour: () => groups.has("behaviour"),
    clock: () => groups.has("clock"),
    activity: () => true,
    import_export: () => false,
  };
  return vocab(env, "tabs").map((t) => ({ ...t, enabled: Boolean((needs[t.key] || (() => false))()) }));
}

export function liveKey(detail) {
  return (detail?.credentials || []).find((c) => c.kind === "api_key" && c.state === "live") || null;
}

export function shortDate(v) {
  return typeof v === "string" ? v.slice(0, 10) : "";
}
