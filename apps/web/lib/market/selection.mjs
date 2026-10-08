/**
 * The market page's selection model (mkt04a Task 4). Pure: no React, no DOM.
 *
 * A selection is an ordered list of { kind, key } — exactly the saved-view
 * config's `selection` entries (docs/MARKET_DATA_DESIGN_V1.md, "Config
 * schema"), so mkt04c can persist it without reshaping:
 *   kind "indicator" -> key is the series_key
 *   kind "security"  -> key is the securities_global id
 * Every operation returns a NEW list of fresh { kind, key } objects; nothing
 * else ever enters an entry.
 *
 * The limit is the server's vocabularies.limits.max_keys. A catalog without a
 * usable limit gets a limit of 0: nothing can be selected (fail closed).
 */

import { KIND_INDICATOR, KIND_SECURITY } from "./marketDefaults.mjs";

export { KIND_INDICATOR, KIND_SECURITY };

const entry = (kind, key) => ({ kind, key });

/** The server's selection limit, or 0 when the catalog does not publish one. */
export function selectionLimit(catalog) {
  const n = catalog?.vocabularies?.limits?.max_keys;
  return Number.isInteger(n) && n > 0 ? n : 0;
}

export function limitMessage(limit) {
  return `You can select up to ${limit} series at a time.`;
}

/** An indicator can be selected when the catalog gives it a series_key. */
export function isSelectableIndicator(ind) {
  return typeof ind?.series_key === "string" && ind.series_key !== "";
}

/** A security can be selected only when the catalog says so AND it is priced. */
export function isSelectableSecurity(sec) {
  return sec?.selectable === true && typeof sec?.series_key === "string" && sec.series_key !== "";
}

/** May this { kind, key } enter a selection, according to the catalog? */
export function isAllowed(catalog, kind, key) {
  if (kind === KIND_INDICATOR) {
    return (catalog?.indicators ?? []).some((i) => isSelectableIndicator(i) && i.series_key === key);
  }
  if (kind === KIND_SECURITY) {
    return (catalog?.securities ?? []).some((s) => isSelectableSecurity(s) && s.id === key);
  }
  return false;
}

export function isSelected(selection, kind, key) {
  return selection.some((s) => s.kind === kind && s.key === key);
}

/** Drop anything the catalog does not allow, duplicates, and extra fields. */
export function sanitize(selection, catalog) {
  const out = [];
  for (const s of Array.isArray(selection) ? selection : []) {
    if (!s || !isAllowed(catalog, s.kind, s.key)) continue;
    if (isSelected(out, s.kind, s.key)) continue;
    out.push(entry(s.kind, s.key));
  }
  return out;
}

/**
 * Toggle one item. Returns { selection, blocked, message }.
 * Adding past the limit, or adding anything the catalog does not allow, is
 * blocked and leaves the selection unchanged.
 */
export function toggle(selection, catalog, kind, key) {
  const current = sanitize(selection, catalog);
  if (isSelected(current, kind, key)) {
    return { selection: current.filter((s) => !(s.kind === kind && s.key === key)), blocked: false, message: null };
  }
  if (!isAllowed(catalog, kind, key)) {
    return { selection: current, blocked: true, message: null };
  }
  const limit = selectionLimit(catalog);
  if (current.length + 1 > limit) {
    return { selection: current, blocked: true, message: limitMessage(limit) };
  }
  return { selection: [...current, entry(kind, key)], blocked: false, message: null };
}

export function clearAll() {
  return [];
}

// ── Grouping ────────────────────────────────────────────────────────────────

/**
 * Categories in the server's sort order, each with its indicators.
 * Indicators match a category by category_key, else by label. An indicator
 * whose category the catalog does not list gets its own group, labelled with
 * the indicator's own category text, so nothing silently disappears.
 */
export function indicatorGroups(catalog) {
  const cats = [...(catalog?.categories ?? [])].sort(
    (a, b) => (a.sort_order ?? 0) - (b.sort_order ?? 0),
  );
  const groups = cats.map((c) => ({ key: c.key, label: c.label, color: c.color, indicators: [] }));
  for (const ind of catalog?.indicators ?? []) {
    let g = groups.find((x) => (ind.category_key != null && x.key === ind.category_key) || x.label === ind.category);
    if (!g) {
      g = { key: ind.category_key ?? ind.category, label: ind.category, color: null, indicators: [] };
      groups.push(g);
    }
    g.indicators.push(ind);
  }
  return groups;
}

/** "all" | "some" | "none" for a category chip, over its SELECTABLE indicators. */
export function categoryState(selection, group) {
  const keys = group.indicators.filter(isSelectableIndicator).map((i) => i.series_key);
  if (keys.length === 0) return "none";
  const n = keys.filter((k) => isSelected(selection, KIND_INDICATOR, k)).length;
  if (n === 0) return "none";
  return n === keys.length ? "all" : "some";
}

/**
 * Bulk chip click. "all" -> clear every indicator in the category; otherwise
 * select every selectable indicator in it. If they do not all fit under the
 * limit, NOTHING is added and the click is blocked with the limit message.
 */
export function toggleCategory(selection, catalog, group) {
  const current = sanitize(selection, catalog);
  const keys = group.indicators.filter(isSelectableIndicator).map((i) => i.series_key);
  if (categoryState(current, group) === "all") {
    return {
      selection: current.filter((s) => !(s.kind === KIND_INDICATOR && keys.includes(s.key))),
      blocked: false,
      message: null,
    };
  }
  const missing = keys.filter((k) => !isSelected(current, KIND_INDICATOR, k));
  const limit = selectionLimit(catalog);
  if (current.length + missing.length > limit) {
    return { selection: current, blocked: true, message: limitMessage(limit) };
  }
  return { selection: [...current, ...missing.map((k) => entry(KIND_INDICATOR, k))], blocked: false, message: null };
}

/**
 * Securities grouped by security_type, in first-appearance order. A group
 * with no selectable member starts collapsed (today: the structured notes).
 */
export function securityGroups(catalog) {
  const groups = [];
  for (const sec of catalog?.securities ?? []) {
    let g = groups.find((x) => x.type === sec.security_type);
    if (!g) {
      g = { type: sec.security_type, securities: [], selectableCount: 0 };
      groups.push(g);
    }
    g.securities.push(sec);
    if (isSelectableSecurity(sec)) g.selectableCount += 1;
  }
  return groups.map((g) => ({ ...g, collapsed: g.selectableCount === 0 }));
}

export function indicatorBySeriesKey(catalog) {
  const map = new Map();
  for (const ind of catalog?.indicators ?? []) {
    if (isSelectableIndicator(ind)) map.set(ind.series_key, ind);
  }
  return map;
}

/**
 * Each selection entry with what the grid needs from the catalog: the series
 * it reads, the label and colour to show, and the first observation date.
 * Entries the catalog does not allow are dropped.
 */
export function resolveSelection(selection, catalog) {
  const bySeries = indicatorBySeriesKey(catalog);
  const out = [];
  for (const s of sanitize(selection, catalog)) {
    if (s.kind === KIND_INDICATOR) {
      const ind = bySeries.get(s.key);
      out.push({
        kind: s.kind, key: s.key, series_key: ind.series_key, label: ind.name, color: ind.color,
        first_observation_date: ind.first_observation_date ?? null,
      });
    } else {
      const sec = catalog.securities.find((x) => x.id === s.key);
      const linked = bySeries.get(sec.series_key);
      out.push({
        kind: s.kind, key: s.key, series_key: sec.series_key, label: sec.name, color: sec.color,
        first_observation_date: linked?.first_observation_date ?? null,
      });
    }
  }
  return out;
}
