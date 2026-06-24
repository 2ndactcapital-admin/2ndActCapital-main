// Entity type values (matching the Postgres `entity_type` enum) and labels.
export const ENTITY_TYPES = [
  { value: "individual", label: "Individual" },
  { value: "trust", label: "Trust" },
  { value: "llc", label: "LLC" },
  { value: "lp", label: "LP" },
  { value: "corporation", label: "Corporation" },
  { value: "foundation", label: "Foundation" },
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
