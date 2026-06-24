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
  { value: "corp_uk", label: "UK Corp" },
  { value: "corp_eu", label: "EU Corp" },
  { value: "corp_cayman", label: "Cayman Corp" },
  { value: "corp_luxembourg", label: "Luxembourg Corp" },
  { value: "corp_other_intl", label: "Intl Corp (Other)" },
  { value: "other", label: "Other" },
];

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
