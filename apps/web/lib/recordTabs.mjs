// NAV3 — the ONE table behind the Entities record page (/crm/[id]): which tabs exist, which entity types and which
// people see each, which follow the page's as-of date, and where the old tab keys went. Pure (node-tested).
//
// A tab is shown only when (a) the entity's type is in its `types` (null = every type), (b) its `needs` are met
// (e.g. a primary household for Cash & Treasury), (c) the person passes its `gate` — the SAME rule as the API behind
// it (canAccess from the nav1 menu module: Super Admin first, fail closed) — and (d) its API has not refused this
// person at run time (`refused`). A tab never renders an empty shell pretending to be data.

import { canAccess, isInternalStrict, isMemberOnly } from "./menuVisibility.mjs";

const ALL = null;
const NOT_HOUSEHOLD = { not: ["household"] };

export const RECORD_TABS = [
  { key: "overview", label: "Overview", types: ALL, gate: null, asOf: false,
    legacy: ["overview"] },
  { key: "holdings", label: "Holdings", types: ALL, gate: null, asOf: true,
    legacy: ["holdings", "liabilities"] },
  { key: "transactions", label: "Transactions", types: ALL, gate: { perm: "view_portfolio", strict: true }, asOf: true,
    legacy: [] },
  { key: "allocation", label: "Allocation & X-ray", types: ALL, gate: null, asOf: true,
    legacy: [] },
  { key: "cash", label: "Cash & Treasury", types: ALL, gate: { anyOf: ["view_portfolio", "manage_portfolio"], strict: true },
    asOf: false, needs: ["household"], legacy: [] },
  { key: "models", label: "Models & rebalancing", types: NOT_HOUSEHOLD, gate: { perm: "view_proposals", strict: true },
    asOf: false, legacy: [] },
  { key: "ownership", label: "Ownership & relationships", types: ALL, gate: null, asOf: true,
    legacy: ["ownership", "relationships"] },
  { key: "details", label: "Details", types: ALL, gate: null, asOf: false,
    legacy: ["addresses", "employment", "tax_ids", "social", "compliance"] },
  { key: "documents", label: "Documents", types: ALL, gate: null, asOf: false,
    legacy: ["documents", "linked_documents"] },
  { key: "activity", label: "Tasks & activity", types: ALL, gate: null, asOf: false,
    legacy: ["notes"] },
];

export const TAB_KEYS = RECORD_TABS.map((t) => t.key);

function typeOk(types, entityType) {
  if (types === null || types === undefined) return true;
  if (Array.isArray(types)) return types.includes(entityType);
  if (types.not) return !types.not.includes(entityType);
  return false;
}

/** The record page itself: the full CRM record is staff-only (GET /entities/{id}/full = internal). */
export function canOpenRecord(me) {
  return isInternalStrict(me);
}

/** The tabs this person sees for this entity, in order. */
export function tabsFor({ me, entityType, hasHousehold = false, refused = [] } = {}) {
  if (!canOpenRecord(me)) return [];
  const no = new Set(refused || []);
  return RECORD_TABS.filter((t) => typeOk(t.types, entityType)
    && (!t.needs || t.needs.every((n) => (n === "household" ? hasHousehold : false)))
    && canAccess(me, t.gate)
    && !no.has(t.key));
}

/** An old `?tab=` key (pre-NAV3 deep links, e.g. the ownership graph's "walk") → the new tab containing it. */
export function tabForKey(key) {
  if (!key) return "overview";
  if (TAB_KEYS.includes(key)) return key;
  const t = RECORD_TABS.find((x) => x.legacy.includes(key));
  return t ? t.key : "overview";
}

/** Pick the active tab: the requested one when it is visible, else the first visible one. */
export function activeTab(requested, visible) {
  const keys = (visible || []).map((t) => t.key);
  const want = tabForKey(requested);
  return keys.includes(want) ? want : (keys[0] || null);
}

// ── One as-of date ─────────────────────────────────────────────────────────────────────────────────────────────
export function todayIso(now = new Date()) {
  const y = now.getFullYear(), m = String(now.getMonth() + 1).padStart(2, "0"), d = String(now.getDate()).padStart(2, "0");
  return `${y}-${m}-${d}`;
}

/** The page's as-of from the URL: a real YYYY-MM-DD not after today, else today (the default). */
export function normalizeAsOf(raw, today = todayIso()) {
  if (typeof raw !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(raw)) return today;
  const [y, m, d] = raw.split("-").map(Number);
  const dt = new Date(Date.UTC(y, m - 1, d));
  if (dt.getUTCFullYear() !== y || dt.getUTCMonth() !== m - 1 || dt.getUTCDate() !== d) return today;
  return raw > today ? today : raw;
}

export function isToday(asOf, today = todayIso()) {
  return !asOf || asOf === today;
}

/** True when this tab follows the as-of date; false → it says "Shows today" whenever the date is not today. */
export function followsAsOf(key) {
  const t = RECORD_TABS.find((x) => x.key === key);
  return Boolean(t && t.asOf);
}

export function showsTodayNote(key, asOf, today = todayIso()) {
  return !isToday(asOf, today) && !followsAsOf(key);
}

/** The record URL with the page parameters (the as-of is dropped when it is today — the default). */
export function recordHref(id, { tab, asOf, today = todayIso(), entityId } = {}) {
  const p = new URLSearchParams();
  if (tab && tab !== "overview") p.set("tab", tab);
  if (asOf && asOf !== today) p.set("as_of", asOf);
  if (entityId) p.set("entity_id", entityId);
  const qs = p.toString();
  return `/crm/${encodeURIComponent(id)}${qs ? `?${qs}` : ""}`;
}

// ── Keyboard (WAI-ARIA tabs: arrows wrap, Home / End) ──────────────────────────────────────────────────────────
export function nextTabIndex(current, key, count) {
  if (!count) return -1;
  if (key === "ArrowRight") return (current + 1) % count;
  if (key === "ArrowLeft") return (current - 1 + count) % count;
  if (key === "Home") return 0;
  if (key === "End") return count - 1;
  return current;
}

// ── Relationship status (entities.status — the KEY; labels from the CRM vocabulary, never hardcoded) ──────────
/** The label for a status key from the server vocabulary (`entity_statuses`); an unknown key shows as stored. */
export function statusLabel(vocab, key) {
  if (!key) return null;
  const list = (vocab && vocab.entity_statuses) || [];
  const hit = list.find((s) => s.value === key);
  return hit ? hit.label : key;
}

/** The status filter options for pickers: "All" + every status in the vocabulary (labels from config). */
export function statusOptions(vocab) {
  return (vocab && Array.isArray(vocab.status_filters)) ? vocab.status_filters : [{ value: "", label: "All" }];
}

/** Cross Book default: staff see clients only unless they chose "All relationships"; a member is unaffected. */
export const CROSS_BOOK_CLIENT_STATUS = "client";
export function crossBookStatus(me, mode) {
  if (isMemberOnly(me)) return null;
  return mode === "all" ? null : CROSS_BOOK_CLIENT_STATUS;
}
export function crossBookSwitchShown(me) {
  return !isMemberOnly(me);
}

// ── Holdings split ─────────────────────────────────────────────────────────────────────────────────────────────
function cents(s) {
  const neg = String(s).trim().startsWith("-");
  const [w, f = ""] = String(s).replace("-", "").split(".");
  const v = BigInt(w || "0") * 100n + BigInt((f + "00").slice(0, 2));
  return neg ? -v : v;
}

/** direct + through vs the total (strings, cents): the difference in cents (0 or a 1-cent rounding at most). */
export function splitDifferenceCents(split) {
  if (!split || !split.direct || !split.through || !split.total) return null;
  const d = cents(split.direct.gross) + cents(split.through.gross) - cents(split.total.gross);
  return Number(d);
}
