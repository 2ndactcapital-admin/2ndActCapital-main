// CPSIM06 — "Open account at custodian" (the SIMULATED custodian): pure helpers over the server's envelope
// (GET /admin/households/{id}/custodian-account). Every list (account types, roles, citizenship, employment, tiers) is
// the server's; nothing here supplies a default that could grant the action. The action exists ONLY when the server
// says can_open === true (fails closed: a lost envelope shows nothing).

export const SIMULATED_LABEL = "SIMULATED";

// The household detail envelope's flag (GET /admin/households/{id}): the action button renders only on exactly true.
export function actionState(detail) {
  const o = detail?.custodian_account_opening;
  if (!o || typeof o !== "object") return { show: false, reason: null };
  if (o.can_open === true && detail?.permissions?.can_write === true) return { show: true, reason: null };
  return { show: false, reason: detail?.permissions?.can_write === true && typeof o.reason === "string" ? o.reason : null };
}

// The form envelope's own gate.
export const canOpen = (env) => env?.permissions?.can_open === true && env?.availability?.available === true;

const list = (v) => (Array.isArray(v) ? v : []);

// YES types enabled; NO types disabled with the custodian's reason (never hidden — the opener sees why).
export function typeOptions(env) {
  const t = env?.vocabularies?.account_types || {};
  return [
    ...list(t.supported).map((o) => ({ key: o.key, label: o.label, disabled: false, reason: null, roles: list(o.roles),
      registration: o.registration_label || null, taxStatus: o.tax_status_label || null })),
    ...list(t.unsupported).map((o) => ({ key: o.key, label: o.label, disabled: true, reason: o.reason || null,
      roles: [], registration: null, taxStatus: null })),
  ];
}

export function rolesFor(env, typeKey) {
  const o = typeOptions(env).find((x) => x.key === typeKey && !x.disabled);
  return o ? o.roles : [];
}

export function roleLabel(env, key) {
  const r = list(env?.vocabularies?.roles).find((x) => x.key === key);
  return r ? r.label : key;
}

// The members a role may be held by: the trust role → a trust (entity) member; every other role → a person.
export function candidatesFor(env, role) {
  return list(env?.members).filter((m) => (role === "trust" ? m.entity_type === "trust" : m.kind === "person")
    && !m.deceased);
}

// Tax id as the page may show it: last four only, with where the full number will come from.
export function taxIdText(member) {
  const t = member?.tax_id || {};
  const l4 = typeof t.last4 === "string" && /^[0-9]{4}$/.test(t.last4) ? t.last4 : null;
  if (t.on_record === "full") return `•••• ${l4} on record — read at send time`;
  if (t.on_record === "hash_only") return `•••• ${l4} on record — type the full number for this request`;
  return "No tax ID on record — type it for this request";
}

export const needsTypedTaxId = (member) => (member?.tax_id?.on_record || "none") !== "full";
export const needsTypedDob = (member) => member?.date_of_birth_on_record !== true;

// Problems from Check / Open, grouped by where the form shows them: "account_type", "owners.<i>", "beneficiaries",
// "beneficiaries.<i>", "trusted_contact", "general". Each keeps its field path and message.
export function groupProblems(problems) {
  const out = {};
  for (const p of list(problems)) {
    const f = String(p?.field || "");
    let key = "general";
    let m;
    if (f === "account_type") key = "account_type";
    else if ((m = f.match(/^owners\[(\d+)\]/))) key = `owners.${m[1]}`;
    else if (f === "owners") key = "owners";
    else if ((m = f.match(/^beneficiaries\[(\d+)\]/))) key = `beneficiaries.${m[1]}`;
    else if (f.startsWith("beneficiaries")) key = "beneficiaries";
    else if (f.startsWith("trusted_contact")) key = "trusted_contact";
    (out[key] = out[key] || []).push({ field: f, message: String(p?.message || ""), code: p?.code || null,
      source: p?.source || null });
  }
  return out;
}

// The short label of a field path ("owners[0].residential_address.postal_code" → "residential address · postal code").
export function fieldLabel(path) {
  return String(path || "").replace(/^(owners|beneficiaries)\[\d+\]\.?/, "").replace(/^trusted_contact\.?/, "")
    .split(".").filter(Boolean).map((s) => s.replace(/_/g, " ")).join(" · ") || "—";
}

// The request body from the form state (no org_id — Rule 6). Typed values only travel when the opener typed them.
export function buildBody(state) {
  const owners = list(state?.owners).filter((o) => o && o.entity_id).map((o) => {
    const ov = {};
    for (const k of ["email", "phone", "citizenship", "visa_type", "visa_expiration", "date_of_birth"]) {
      if (o.overrides?.[k]) ov[k] = o.overrides[k];
    }
    if (o.overrides?.employment_status) {
      ov.employment = { status: o.overrides.employment_status, employer: o.overrides.employer || null,
        occupation: o.overrides.occupation || null };
    }
    if (o.overrides?.tax_id_value) ov.tax_id = { kind: o.overrides.tax_id_kind || null, value: o.overrides.tax_id_value };
    return { entity_id: o.entity_id, role: o.role, ...(Object.keys(ov).length ? { overrides: ov } : {}) };
  });
  const beneficiaries = list(state?.beneficiaries).filter((b) => b && (b.name || b.entity_id)).map((b) => ({
    tier: b.tier || null, name: b.name || null, kind: b.kind || null, entity_id: b.entity_id || null,
    relationship: b.relationship || null, percentage: b.percentage === "" || b.percentage == null ? null : String(b.percentage),
  }));
  const t = state?.trusted_contact;
  const trusted = t && (t.name || t.email || t.phone || t.entity_id)
    ? { name: t.name || null, email: t.email || null, phone: t.phone || null, entity_id: t.entity_id || null } : null;
  return { account_type: state?.account_type || "", owners, beneficiaries, trusted_contact: trusted,
    ...(state?.draft_reference ? { draft_reference: state.draft_reference } : {}) };
}

// Initial form state from the envelope: one owner slot per role of the chosen type, pre-picked when the CRM implies one
// (the only trust member; its first trustee; a minor's parent); beneficiaries the CRM records for the chosen owner.
export function initialState(env, typeKey) {
  const roles = rolesFor(env, typeKey);
  const sug = env?.suggestions || {};
  const owners = roles.map((role) => ({ role, entity_id: "", overrides: {} }));
  const pick = (role, id) => { const o = owners.find((x) => x.role === role && !x.entity_id); if (o && id) o.entity_id = id; };
  if (roles.includes("trust")) {
    const trusts = candidatesFor(env, "trust");
    if (trusts.length === 1) {
      pick("trust", trusts[0].entity_id);
      pick("trustee", list(sug.trustees?.[trusts[0].entity_id])[0]);
    }
  }
  if (roles.includes("minor")) {
    const minors = candidatesFor(env, "minor").filter((m) => m.is_minor === true);
    if (minors.length === 1) {
      pick("minor", minors[0].entity_id);
      pick("custodian", list(sug.parents?.[minors[0].entity_id])[0]);
    }
  }
  const first = owners[0]?.entity_id;
  const beneficiaries = first ? list(sug.beneficiaries?.[first]).map((b) => ({ tier: b.tier || "primary", name: b.name || "",
    entity_id: b.entity_id || "", kind: "", relationship: "", percentage: "" })) : [];
  return { account_type: typeKey || "", owners, beneficiaries, trusted_contact: { name: "", email: "", phone: "" } };
}

// The outcome line after Open.
export function openedText(result) {
  if (!result?.opened) return null;
  const a = result.account || {};
  const c = result.counterparty || {};
  const s = result.sync || {};
  return `${result.repeat ? "Already open" : "Opened"} at the simulated custodian: ${a.account_number_masked || "****"}`
    + ` · ${c.account_type || ""} · ${c.status || ""} since ${c.date_open || "—"}`
    + ` · ${s.synced ? "synced" : `not synced${s.reason ? ` (${s.reason})` : ""}`}`;
}
