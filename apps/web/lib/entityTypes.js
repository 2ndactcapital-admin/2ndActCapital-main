// Entity type values (matching the Postgres `entity_type` enum) and labels.
export const ENTITY_TYPES = [
  { value: "individual", label: "Individual" },
  { value: "trust", label: "Trust" },
  { value: "llc", label: "LLC" },
  { value: "lp", label: "LP" },
  { value: "gp", label: "GP" },
  { value: "s_corp", label: "S-Corp" },
  { value: "c_corp", label: "C-Corp" },
  { value: "corporation", label: "Corporation" },
  { value: "foundation", label: "Foundation" },
  { value: "family_office", label: "Family Office" },
  { value: "household", label: "Household / Family Group" },
  { value: "corp_uk", label: "UK Corp" },
  { value: "corp_eu", label: "EU Corp" },
  { value: "corp_cayman", label: "Cayman Corp" },
  { value: "corp_luxembourg", label: "Luxembourg Corp" },
  { value: "corp_other_intl", label: "Intl Corp (Other)" },
  { value: "other", label: "Other" },
];

// Entity types that represent a natural person (affects name-field layout).
export const PERSON_TYPES = new Set(["individual"]);

// Entity types that can hold an investment / indicate interest in a deal.
// Used to filter the entity selector in the IOI and compliance-review modals so
// members only pick from their own investing vehicles (not sponsors, funds, etc).
export const INVESTOR_ENTITY_TYPES = [
  "individual",
  "trust",
  "llc",
  "lp",
  "household",
  "family_office",
];

export function isInvestorEntity(entity) {
  return INVESTOR_ENTITY_TYPES.includes(entity?.entity_type);
}

// Tabs shown on the CRM list (per spec — excludes "other").
export const FILTER_TABS = [
  { value: "", label: "All" },
  { value: "individual", label: "Individual" },
  { value: "trust", label: "Trust" },
  { value: "llc", label: "LLC" },
  { value: "lp", label: "LP" },
  { value: "corporation", label: "Corporation" },
  { value: "foundation", label: "Foundation" },
];

export function typeLabel(value) {
  return ENTITY_TYPES.find((t) => t.value === value)?.label || value;
}

// PORTENTRY1 — entity statuses, marital statuses and sub-types are NOT here any more: they are per-org data in the
// config table, served in the API envelope (GET /api/v1/crm/vocabularies) and read through lib/crmVocab.mjs.
