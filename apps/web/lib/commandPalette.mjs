/**
 * NAV2 — Ctrl K: pure model (node-testable). Three kinds of result:
 *   * Records — entities from GET /entities/search (member + VISIBILITY1 staff scope in SQL) and securities from the
 *     existing market security search (GET /market/securities/search, require_market_data_read), each labelled.
 *   * Screens — from the ONE menu definition (lib/menuVisibility.mjs), filtered by the person's gates through
 *     visibleMenuItems(me): never a screen they cannot open. No second list.
 *   * "Ask about <selected entity>…" — opens the assistant with that entity as its context (only for a VISIBLE one).
 */
import { displayLabel, GROUPS, visibleMenuItems } from "./menuVisibility.mjs";
import { typeLabel, withEntity } from "./entityContext.mjs";

const norm = (s) => String(s || "").toLowerCase();

/** Every screen `me` may open: { href, label, group } in menu order. Exactly the visible menu hrefs. */
export function screensFor(me) {
  return visibleMenuItems(me).map((i) => {
    const g = i.group ? GROUPS.find((x) => x.id === i.group) : null;
    return { href: i.href, label: displayLabel(me, i), group: g ? displayLabel(me, g) : null };
  });
}

/** Screens matching `query` (every word in the label or group); all of them for an empty query. */
export function matchScreens(me, query) {
  const words = norm(query).split(/\s+/).filter(Boolean);
  const all = screensFor(me);
  if (!words.length) return all;
  return all.filter((s) => {
    const hay = `${norm(s.label)} ${norm(s.group)}`;
    return words.every((w) => hay.includes(w));
  });
}

/**
 * The flat option list the dialog renders and the arrow keys walk, in section order.
 * entities / securities: already-fetched results; selected: chipState() of the selected entity.
 */
export function paletteOptions({ me, query, entities = [], securities = [], selected = null, carryId = null }) {
  const opts = [];
  if (selected && selected.kind === "entity") {
    opts.push({ section: "Ask", kind: "ask", key: `ask:${selected.id}`, label: `Ask about ${selected.name}…`,
      entity: { id: selected.id, display_name: selected.name, entity_type: selected.type } });
  }
  for (const e of entities) {
    opts.push({ section: "Records", kind: "entity", key: `entity:${e.id}`, label: e.display_name,
      detail: typeLabel(e.entity_type) || "Entity", entity: e });
  }
  for (const s of securities) {
    opts.push({ section: "Records", kind: "security", key: `security:${s.id}`,
      label: s.ticker ? `${s.ticker} — ${s.name}` : s.name, detail: "Security", href: withEntity("/market", carryId) });
  }
  for (const s of matchScreens(me, query)) {
    opts.push({ section: "Screens", kind: "screen", key: `screen:${s.href}`, label: s.label,
      detail: s.group || "", href: withEntity(s.href, carryId) });
  }
  return opts;
}

/** True when the key event's target is a place the person types — the palette never steals those keys. */
export function isTypingTarget(target) {
  if (!target) return false;
  const tag = String(target.tagName || "").toUpperCase();
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return true;
  return !!target.isContentEditable;
}

/** Ctrl/Cmd + K, outside a typing target. */
export function isOpenShortcut(e) {
  if (!e || !(e.ctrlKey || e.metaKey) || e.altKey || e.shiftKey) return false;
  if (String(e.key || "").toLowerCase() !== "k") return false;
  return !isTypingTarget(e.target);
}

/** The assistant context for a selected entity — the EXISTING context_ref shape ({ type, id }) plus its type. */
export function assistantContextFor(state) {
  if (!state || state.kind !== "entity") return null;
  return { type: "entity", id: state.id, entity_type: state.type || null };
}
