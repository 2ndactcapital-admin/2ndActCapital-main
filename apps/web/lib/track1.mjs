// TRACK1 — "Track this security". Pure helpers shared by the Security master's Tracked view and the Market
// Indicators search result. Imports nothing, so plain node tests exercise the exact code the pages run.
//
// FAIL CLOSED everywhere: a write control exists only when the server's envelope says so with a literal `true`
// (permissions.can_track / can_untrack); a missing or malformed envelope → no control. Every bulk action shows the
// server's count and estimate and asks to confirm; the request then carries that count (expected_total) and the
// server refuses if the selection changed in between. Identity is never sent: the forward attaches the session.

export const MASTER_PATH = "/admin/security-data/master";
export const TRACKED_API = "/api/admin/security-data/master/tracked";
export const ESTIMATE_API = `${TRACKED_API}/estimate`;
export const TRACK_API = `${TRACKED_API}/track`;
export const UNTRACK_API = `${TRACKED_API}/untrack`;

const isObj = (v) => v !== null && typeof v === "object" && !Array.isArray(v);
const num = (n) => (typeof n === "number" && Number.isFinite(n) ? n.toLocaleString("en-US") : "—");

export function canTrack(env) {
  return isObj(env) && isObj(env.permissions) && env.permissions.can_track === true;
}

export function canUntrack(env) {
  return isObj(env) && isObj(env.permissions) && env.permissions.can_untrack === true;
}

// The server's filter names (vocabularies.filters) — anything else never reaches a URL or a request.
export function allowedFilters(env) {
  const f = env?.vocabularies?.filters;
  return Array.isArray(f) ? f.filter((x) => typeof x === "string") : [];
}

export function cleanTrackedFilter(filter, env) {
  const out = {};
  for (const k of allowedFilters(env)) {
    const v = filter?.[k];
    if (typeof v === "string" && v.trim() !== "") out[k] = v.trim();
  }
  return out;
}

// The Tracked view's page link for a filter (the view is selected by `view=tracked`).
export function trackedHref(filter, env) {
  const f = cleanTrackedFilter(filter, env);
  return `${MASTER_PATH}?${new URLSearchParams({ view: "tracked", ...f }).toString()}`;
}

// A selection body — exactly one of ids / filters / list_key (the server refuses anything else).
export function selectionBody({ ids, filters, listKey }, env) {
  if (Array.isArray(ids) && ids.length) return { ids: [...new Set(ids.map(String))] };
  if (typeof listKey === "string" && listKey) return { list_key: listKey };
  const f = cleanTrackedFilter(filters, env);
  return Object.keys(f).length ? { filters: f } : null;
}

// The confirmation text for a bulk action, from the server's estimate envelope. Over the cap → null (the caller
// shows `refusalText` instead; there is nothing to confirm).
export function confirmText(verb, est) {
  if (!isObj(est) || typeof est.total !== "number") return null;
  if (est.over_cap === true) return null;
  const e = est.estimate;
  const lines = [`${verb === "untrack" ? "Untrack" : "Track"} ${num(est.total)} securities?`];
  if (isObj(e)) {
    if (verb === "untrack") {
      lines.push(`The coverage set goes from ${num(e.coverage_now)} to ${num(e.coverage_after)}. Loaded prices are kept.`);
    } else {
      lines.push(`${num(e.to_load)} need their 5-year history: ${num(e.estimated_calls)} calls to ${e.provider || "the price source"}, ` +
        `${num(e.days_at_budget)} day(s) at the daily budget of ${num(e.daily_budget)}` +
        (e.nights_nightly_only != null ? ` (${num(e.nights_nightly_only)} night(s) through the nightly job alone).` : "."));
      lines.push(`Afterwards ${num(e.coverage_after)} securities are covered: ${num(e.upkeep_calls_per_day)} calls a day to keep current.`);
    }
    for (const w of Array.isArray(e.warnings) ? e.warnings : []) if (typeof w === "string") lines.push(`Note: ${w}`);
  }
  return lines.join("\n");
}

export function refusalText(est) {
  if (!isObj(est) || est.over_cap !== true) return null;
  return `${num(est.total)} securities match — one action may change at most ${num(est.cap)}. Narrow the filter.`;
}

// The request body for the confirmed action: the selection + the confirmed count + the note.
export function actionBody(selection, est, note) {
  if (!selection || !isObj(est) || typeof est.total !== "number" || est.over_cap === true) return null;
  const n = typeof note === "string" ? note.trim() : "";
  return { ...selection, expected_total: est.total, ...(n ? { note: n } : {}) };
}

// ── Market Indicators search result ─────────────────────────────────────────
// The search response's `tracking` block → { canTrack, reason, tracked:Set, note } (fail closed: no block → no
// control, no reason).
export function searchTracking(body) {
  const t = isObj(body) ? body.tracking : null;
  if (!isObj(t)) return null;
  return {
    canTrack: t.can_track === true,
    reason: typeof t.disabled_reason === "string" ? t.disabled_reason : null,
    tracked: new Set(Array.isArray(t.tracked) ? t.tracked.map(String) : []),
    note: typeof t.note === "string" ? t.note : null,
  };
}

// What an UNSELECTABLE search result offers: null (nothing) | {kind:"tracked"} | {kind:"track"} |
// {kind:"disabled", reason}.
export function trackOffer(tracking, sec, trackedNow) {
  if (!tracking || !isObj(sec) || sec.selectable === true) return null;
  const id = String(sec.id);
  if (tracking.tracked.has(id) || (trackedNow && trackedNow.has(id))) return { kind: "tracked" };
  if (tracking.canTrack) return { kind: "track" };
  return tracking.reason ? { kind: "disabled", reason: tracking.reason } : null;
}

export function singleTrackBody(sec) {
  return { ids: [String(sec.id)], expected_total: 1, note: "Tracked from the Market Indicators search" };
}
