/**
 * Key dates and "My dates" for the chart (mkt04c). Pure: no React, no DOM, no
 * fetch.
 *
 *   options       the two dropdowns: reference key dates and the caller's own
 *                 dates, each in the order the server returned them.
 *   selection     {source: "key" | "my", id, edge: "start" | "end"} — which
 *                 date is picked and, for a range, which end of it. Every new
 *                 pick starts at "start".
 *   periods       a picked date moves the anchor to the chart period that
 *                 CONTAINS it (the first period ending on or after it, clamped
 *                 to the ends), so a day-precision date lands on its own month
 *                 at the monthly resolution.
 *   flag          "Anchor · Mon YYYY · Name" while the picked date is the
 *                 anchor's period, "(end)" appended for an end-of-period anchor.
 *
 * Names, dates and precisions are the server's; nothing is sorted or renamed.
 */

import { monthLabel } from "./scales.mjs";
import { PRECISION_MONTH } from "./viewContract.mjs";

export const SOURCE_KEY = "key";
export const SOURCE_MY = "my";
export const EDGE_START = "start";
export const EDGE_END = "end";
export const END_SUFFIX = " (end)";
export const SEP = " · ";
export const RANGE_DASH = " – ";

const ISO = /^\d{4}-\d{2}-\d{2}$/;
const isIso = (v) => typeof v === "string" && ISO.test(v);

/** "2020-03-16" -> "Mar 16, 2020"; month precision -> "Mar 2020". */
export function formatKeyDate(iso, precision) {
  if (!isIso(iso)) return "";
  const month = monthLabel(iso); // "Mar 2020"
  if (precision === PRECISION_MONTH) return month;
  const [mon, year] = month.split(" ");
  return `${mon} ${iso.slice(8, 10).replace(/^0/, "")}, ${year}`;
}

export function isRangeEntry(k) {
  return isIso(k?.end_date);
}

/** Reference key dates as dropdown options, in the server's order. */
export function keyDateOptions(keyDates) {
  return (Array.isArray(keyDates) ? keyDates : [])
    .filter((k) => typeof k?.slug === "string" && isIso(k.start_date))
    .map((k) => {
      const start = formatKeyDate(k.start_date, k.start_precision);
      const range = isRangeEntry(k);
      const when = range ? `${start}${RANGE_DASH}${formatKeyDate(k.end_date, k.end_precision)}` : start;
      return { value: k.slug, label: `${when}${SEP}${k.name}`, isRange: range };
    });
}

/** The caller's own dates as dropdown options, in the server's order. */
export function customDateOptions(customDates) {
  return (Array.isArray(customDates) ? customDates : [])
    .filter((c) => typeof c?.id === "string" && isIso(c.event_date))
    .map((c) => ({ value: c.id, label: `${formatKeyDate(c.event_date, null)}${SEP}${c.name}` }));
}

// ── Selection ───────────────────────────────────────────────────────────────

/** A new pick always starts at the start of the period. "" clears. */
export function pickDate(source, id) {
  return typeof id === "string" && id !== "" ? { source, id, edge: EDGE_START } : null;
}

export function setEdge(sel, edge) {
  return sel ? { ...sel, edge: edge === EDGE_END ? EDGE_END : EDGE_START } : null;
}

/**
 * The picked entry against the key-dates state, or null when nothing is
 * picked or the entry is gone:
 *   {source, id, name, isRange, edge, target, band: {start, end} | null}
 * target is the date the anchor moves to; band is the whole range.
 */
export function resolveDateSelection(sel, kd) {
  if (!sel || kd?.kind !== "ready") return null;
  if (sel.source === SOURCE_KEY) {
    const k = (kd.keyDates ?? []).find((x) => x?.slug === sel.id);
    if (!k || !isIso(k.start_date)) return null;
    const range = isRangeEntry(k);
    const edge = range && sel.edge === EDGE_END ? EDGE_END : EDGE_START;
    return {
      source: SOURCE_KEY,
      id: sel.id,
      name: k.name,
      isRange: range,
      edge,
      target: edge === EDGE_END ? k.end_date : k.start_date,
      band: range ? { start: k.start_date, end: k.end_date } : null,
    };
  }
  if (sel.source === SOURCE_MY) {
    const c = (kd.customDates ?? []).find((x) => x?.id === sel.id);
    if (!c || !isIso(c.event_date)) return null;
    return { source: SOURCE_MY, id: sel.id, name: c.name, isRange: false, edge: EDGE_START, target: c.event_date, band: null };
  }
  return null;
}

// ── Periods ─────────────────────────────────────────────────────────────────

/**
 * The index of the chart period containing iso: the first period ending on or
 * after it; a date after the last period clamps to the last, one before the
 * first lands on the first. -1 without periods.
 */
export function periodIndexFor(periods, iso) {
  if (!Array.isArray(periods) || periods.length === 0) return -1;
  if (!isIso(iso)) return 0;
  for (let i = 0; i < periods.length; i += 1) if (periods[i].date >= iso) return i;
  return periods.length - 1;
}

/** The anchor date a picked date moves to: its period's date (iso when there are no periods). */
export function anchorDateFor(periods, iso) {
  const i = periodIndexFor(periods, iso);
  return i < 0 ? iso : periods[i].date;
}

/** The anchor flag: the date, plus the picked date's name while it is the anchor. */
export function anchorFlagText(anchorDate, anchorIndex, periods, resolved) {
  const base = `Anchor${SEP}${monthLabel(anchorDate)}`;
  if (!resolved || periodIndexFor(periods, resolved.target) !== anchorIndex) return base;
  return `${base}${SEP}${resolved.name}${resolved.edge === EDGE_END ? END_SUFFIX : ""}`;
}

/** The span to shade for a picked range ({start, end}), or null. Shown whatever the overlays. */
export function selectedBandRange(resolved) {
  return resolved?.band ?? null;
}
