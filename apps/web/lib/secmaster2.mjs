// SECMASTER2 — the Security master summary's links. Every count on the summary links to the filtered
// list of exactly those securities; the filter is the server's own (each count arrives with its `filter`),
// and only filter names the server lists (envelope.list_filters) ever reach a URL or a request — anything
// else is dropped, so a hand-edited query string cannot widen what the page asks for (fails closed).

export const MASTER_PATH = "/admin/security-data/master";
export const LIST_API = "/api/admin/security-data/master/securities";
export const REVIEW_API = "/api/admin/security-data/master/review";

const listed = (allowed) => (Array.isArray(allowed) ? allowed : []);

// Only the allowed keys, with a non-empty string value, in a stable order.
export function cleanFilter(filter, allowed) {
  const ok = listed(allowed);
  const out = {};
  for (const k of ok) {
    const v = filter?.[k];
    if (typeof v === "string" && v.trim() !== "") out[k] = v.trim();
  }
  return out;
}

const qs = (o) => new URLSearchParams(o).toString();

// The page link for a count: the master page with the list's filter in its query (null when nothing is left).
export function listHref(filter, allowed) {
  const f = cleanFilter(filter, allowed);
  return Object.keys(f).length ? `${MASTER_PATH}?${qs(f)}` : null;
}

// The review-queue link for a kind (the review filter the server sent, kind checked against its vocabulary).
export function reviewHref(reviewFilter, kinds) {
  const k = reviewFilter?.kind;
  const known = (Array.isArray(kinds) ? kinds : []).some((x) => x?.key === k);
  return known ? `${MASTER_PATH}?${qs({ review_kind: k })}` : null;
}

// The list request for the page's search params (a plain object), or null when no allowed filter is present.
export function listRequest(search, allowed, { limit = 100, offset = 0 } = {}) {
  const f = cleanFilter(search, allowed);
  if (!Object.keys(f).length) return null;
  const off = Number.isFinite(Number(offset)) && Number(offset) > 0 ? Math.floor(Number(offset)) : 0;
  return `${LIST_API}?${qs({ ...f, limit: String(limit), offset: String(off) })}`;
}

// A plain-words description of a list filter, from the server's own labels where it has them.
export function describeFilter(filter, labels) {
  const f = filter || {};
  const L = labels || {};
  return Object.entries(f)
    .map(([k, v]) => `${L[k] || k.replace(/_/g, " ")}: ${v}`)
    .join(" · ");
}

export const pct = (part, whole) => (whole > 0 ? `${Math.round((1000 * part) / whole) / 10}%` : "—");
