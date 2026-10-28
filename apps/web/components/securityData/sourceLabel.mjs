// MARKETDATA1 — the ONE hover line for a priced figure: source + as-of, or what is missing.
// Takes a security-data layer result (Result.as_dict()) or a {provider_name, as_of} pair.
export function sourceTitle(r) {
  if (!r) return "not priced";
  if (r.found === false) return r.label || `not priced — ${r.missing || "no source"}`;
  const who = r.provider_name || r.provider_key || "unknown source";
  const when = r.as_of ? `, as of ${r.as_of}` : "";
  const q = Array.isArray(r.quality) && r.quality.length ? ` (${r.quality.join(", ")})` : "";
  return `estimate — ${who}${when}${q}`;
}

export const can = (env, flag) => env?.permissions?.[flag] === true;
export const vocab = (env, key) => (Array.isArray(env?.vocabularies?.[key]) ? env.vocabularies[key] : []);
export const labelOf = (list, key) => (list || []).find((x) => x.key === key)?.label || key;
