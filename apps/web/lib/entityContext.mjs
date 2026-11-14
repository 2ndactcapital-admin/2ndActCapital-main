/**
 * NAV2 — the ONE selected-entity context: pure, dependency-free (node-testable), the one rule.
 *
 *   * The context is a single URL parameter, `entity_id` — the parameter app/portfolio/page.js already read before
 *     NAV2. Every link the shell draws carries it forward when set (`withEntity`) and drops it when cleared.
 *   * The URL ALWAYS wins. A remembered selection (per person, per browser — there is no generic user-preference
 *     column; users.nav_pinned / users.assistant_panel_posture are single-purpose) only applies when the URL carries
 *     no `entity_id` at all, and then the shell writes it INTO the URL (so the page and the chip never disagree).
 *   * An id the person cannot see — missing, forbidden or malformed — gives the SAME answer: the chip reads
 *     "Not available" and every screen renders as if no entity were set (`effectiveEntityId` returns null). The name
 *     is never shown, so existence is never disclosed.
 *   * Storage access is wrapped in try/catch (private mode, disabled storage) and keyed by the person's user id so
 *     two people sharing a browser never inherit each other's selection.
 */
export const PARAM = "entity_id";
export const RECENT_MAX = 8;
export const NOT_AVAILABLE = "Not available";

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
export const isEntityId = (v) => typeof v === "string" && UUID_RE.test(v);

export const selectionKey = (userId) => `nav2-entity:${userId || "anon"}`;
export const recentKey = (userId) => `nav2-recent:${userId || "anon"}`;

/** The `entity_id` the URL carries: undefined when absent, the raw string (possibly malformed) when present. */
export function urlEntityParam(search) {
  try {
    const sp = typeof search === "string" ? new URLSearchParams(search) : search;
    if (!sp || typeof sp.get !== "function") return undefined;
    if (typeof sp.has === "function" ? !sp.has(PARAM) : sp.get(PARAM) == null) return undefined;
    return sp.get(PARAM) ?? "";
  } catch {
    return undefined;
  }
}

/**
 * The id the shell works with. The URL wins whenever it carries the parameter (even an empty or malformed value —
 * that is "nothing", never "fall back to the remembered one"); the remembered value only when the URL has none.
 * Returns { id, source: "url" | "remembered" | "none" }.
 */
export function resolveSelection({ urlValue, remembered } = {}) {
  if (urlValue !== undefined) return { id: urlValue || null, source: urlValue ? "url" : "none" };
  if (isEntityId(remembered)) return { id: remembered, source: "remembered" };
  return { id: null, source: "none" };
}

/** True when the shell should write the remembered selection into the URL (replace, not push). */
export const shouldAdoptRemembered = (sel) => sel.source === "remembered";

/**
 * An href with the context carried (entityId set) or dropped (entityId null). Leaves external links, protocol links,
 * auth routes and API routes untouched; keeps every other query parameter and the hash.
 */
export function withEntity(href, entityId) {
  if (typeof href !== "string" || !href.startsWith("/") || href.startsWith("//")) return href;
  if (href.startsWith("/auth/") || href.startsWith("/api/")) return href;
  const hashAt = href.indexOf("#");
  const hash = hashAt >= 0 ? href.slice(hashAt) : "";
  const noHash = hashAt >= 0 ? href.slice(0, hashAt) : href;
  const qAt = noHash.indexOf("?");
  const path = qAt >= 0 ? noHash.slice(0, qAt) : noHash;
  const sp = new URLSearchParams(qAt >= 0 ? noHash.slice(qAt + 1) : "");
  if (entityId) sp.set(PARAM, entityId);
  else sp.delete(PARAM);
  const qs = sp.toString();
  return `${path}${qs ? `?${qs}` : ""}${hash}`;
}

// ── chip state ────────────────────────────────────────────────────────────────

/**
 * What the chip shows, from the selection and the lookup result.
 *   lookup: undefined (not asked yet / in flight) | null (missing, forbidden or malformed — ONE answer) |
 *           { id, display_name, entity_type }
 * Returns { kind: "none" | "loading" | "entity" | "unavailable", id, name, type }.
 */
export function chipState(id, lookup) {
  if (!id) return { kind: "none", id: null, name: null, type: null };
  if (!isEntityId(id)) return { kind: "unavailable", id: null, name: null, type: null };
  if (lookup === undefined) return { kind: "loading", id, name: null, type: null };
  if (!lookup || String(lookup.id) !== String(id)) return { kind: "unavailable", id: null, name: null, type: null };
  return { kind: "entity", id, name: lookup.display_name || "", type: lookup.entity_type || null };
}

/** The id a SCREEN may filter on: only a resolved, visible entity — never a pending, forbidden or missing one. */
export const effectiveEntityId = (state) => (state && state.kind === "entity" ? state.id : null);

/** A lookup response reduced to the three fields the chip needs; anything else (non-2xx, malformed) → null. */
export function reduceLookup(ok, data) {
  if (!ok || !data) return null;
  const e = data.entity || data;
  if (!e || !isEntityId(String(e.id || ""))) return null;
  return { id: String(e.id), display_name: e.display_name || "", entity_type: e.entity_type || null };
}

// ── Recent (last RECENT_MAX opened, most recent first) ────────────────────────

export function pushRecent(list, item) {
  if (!item || !isEntityId(String(item.id || ""))) return Array.isArray(list) ? list.slice(0, RECENT_MAX) : [];
  const entry = { id: String(item.id), display_name: item.display_name || "", entity_type: item.entity_type || null };
  const rest = (Array.isArray(list) ? list : []).filter((r) => r && String(r.id) !== entry.id);
  return [entry, ...rest].slice(0, RECENT_MAX);
}

export function readRecent(storage, userId) {
  try {
    const raw = storage ? storage.getItem(recentKey(userId)) : null;
    const v = raw ? JSON.parse(raw) : [];
    return Array.isArray(v) ? v.filter((r) => r && isEntityId(String(r.id || ""))).slice(0, RECENT_MAX) : [];
  } catch {
    return [];
  }
}

export function writeRecent(storage, userId, list) {
  try {
    if (storage) storage.setItem(recentKey(userId), JSON.stringify((list || []).slice(0, RECENT_MAX)));
  } catch {
    // storage unavailable — Recent is a convenience only
  }
}

export function readRemembered(storage, userId) {
  try {
    const v = storage ? storage.getItem(selectionKey(userId)) : null;
    return isEntityId(v) ? v : null;
  } catch {
    return null;
  }
}

export function writeRemembered(storage, userId, id) {
  try {
    if (!storage) return;
    if (isEntityId(id)) storage.setItem(selectionKey(userId), id);
    else storage.removeItem(selectionKey(userId));
  } catch {
    // storage unavailable — the URL still carries the context
  }
}

/** Remove a no-longer-visible id from Recent (so a forbidden name never lingers there). */
export const dropRecent = (list, id) => (Array.isArray(list) ? list.filter((r) => r && String(r.id) !== String(id)) : []);

// ── labels ────────────────────────────────────────────────────────────────────

/** "household" → "Household", "legal_entity" → "Legal entity". Display only; the type key comes from the API. */
export function typeLabel(type) {
  if (!type) return "";
  const s = String(type).replace(/_/g, " ");
  return s.charAt(0).toUpperCase() + s.slice(1);
}

export const filteredBanner = (name) => `Filtered to ${name} — clear the chip to see everything you can see`;
export const NOT_FILTERED_NOTICE = "This screen isn't filtered by entity";

/**
 * Where choosing an entity takes the person: the same screen with the parameter set when that screen has an entity
 * view, else the entity's own page (CRM record — staff) or the Cross Book overview (which filters by entity_id).
 */
export function destinationFor({ pathname, search, entityId, screenFilters, canOpenRecord }) {
  if (screenFilters) return withEntity(`${pathname}${search ? (search.startsWith("?") ? search : `?${search}`) : ""}`, entityId);
  if (canOpenRecord) return withEntity(`/crm/${entityId}`, entityId);
  return withEntity("/portfolio", entityId);
}
