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

// Entity lifecycle status.
export const STATUS_OPTIONS = [
  { value: "prospect", label: "Prospect" },
  { value: "active", label: "Active" },
  { value: "inactive", label: "Inactive" },
  { value: "archived", label: "Archived" },
];

// Status filter pills for the CRM list (includes "All").
export const STATUS_FILTERS = [{ value: "", label: "All" }, ...STATUS_OPTIONS];

export function statusLabel(value) {
  return STATUS_OPTIONS.find((s) => s.value === value)?.label || value;
}

// Sub-types available per entity type.
export const SUBTYPES_BY_TYPE = {
  trust: [
    "Revocable",
    "Irrevocable",
    "Charitable",
    "GRAT",
    "SLAT",
    "QTIP",
    "Land Trust",
    "Other",
  ],
  llc: ["Single-member", "Multi-member", "Series LLC", "Other"],
  lp: ["Family LP", "Fund LP", "Other"],
  gp: ["Family GP", "Fund GP", "Other"],
  corp_uk: ["Ltd", "PLC", "LLP", "Other"],
  corp_eu: ["GmbH", "SA", "SAS", "BV", "NV", "SpA", "Other"],
  corp_cayman: ["Exempted Company", "LLC", "Exempted LP", "Other"],
  corp_luxembourg: ["SARL", "SA", "SCSp", "SCA", "Other"],
  family_office: ["Single-family", "Multi-family"],
  // s_corp, c_corp: no sub-types
  // corp_other_intl: free-text sub_type
};

// Entity types whose sub_type is entered as free text rather than chosen.
export const FREE_TEXT_SUBTYPE_TYPES = ["corp_other_intl"];

export function subTypesFor(type) {
  return SUBTYPES_BY_TYPE[type] || [];
}
