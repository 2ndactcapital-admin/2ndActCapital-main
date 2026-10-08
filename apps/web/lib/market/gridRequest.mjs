/**
 * Building and sequencing POST /api/market/grid requests (mkt04a Task 5).
 * Pure: no React, no DOM, no fetch. Timers are injected so a test can drive
 * them.
 *
 * The body carries exactly the fields the API accepts — keys, anchor, mode,
 * frequency, and end only when the user set one. Never an org or user: those
 * come from the session, server-side.
 */

import {
  DEFAULT_ANCHOR_YEARS_BACK,
  DEFAULT_FREQUENCY,
  DEFAULT_MODE,
} from "./marketDefaults.mjs";
import { resolveSelection } from "./selection.mjs";

export const GRID_BODY_FIELDS = ["keys", "anchor", "end", "mode", "frequency"];
export const GRID_DEBOUNCE_MS = 300;

const ISO = /^(\d{4})-(\d{2})-(\d{2})$/;
const pad = (n, w = 2) => String(n).padStart(w, "0");

/** Today's calendar date in the browser's own time zone, as YYYY-MM-DD. */
export function todayIso(now = new Date()) {
  return `${pad(now.getFullYear(), 4)}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
}

/** The same calendar day `years` earlier; 29 February falls back to the 28th. */
export function yearsAgo(iso, years) {
  const m = ISO.exec(iso);
  if (!m) return null;
  const y = Number(m[1]) - years;
  const mo = Number(m[2]);
  let d = Number(m[3]);
  // 29 February exists in year y only if Date keeps it in February.
  if (mo === 2 && d === 29 && new Date(Date.UTC(y, 1, 29)).getUTCMonth() !== 1) d = 28;
  return `${pad(y, 4)}-${pad(mo)}-${pad(d)}`;
}

/** The earliest first_observation_date among the resolved selection, or null. */
export function startOfData(resolved) {
  const dates = resolved
    .map((r) => r.first_observation_date)
    .filter((d) => typeof d === "string" && ISO.test(d))
    .sort();
  return dates[0] ?? null;
}

function vocabKeys(list) {
  return Array.isArray(list) ? list.map((v) => v?.key).filter((k) => typeof k === "string") : [];
}

/** A default when the server still offers it, else the server's first option. */
function pick(preferred, keys) {
  if (keys.includes(preferred)) return preferred;
  return keys[0] ?? null;
}

/** The grid controls on first load. */
export function initialControls(catalog, today) {
  const v = catalog?.vocabularies ?? {};
  return {
    mode: pick(DEFAULT_MODE, vocabKeys(v.modes)),
    frequency: pick(DEFAULT_FREQUENCY, vocabKeys(v.grid_frequencies)),
    anchor: yearsAgo(today, DEFAULT_ANCHOR_YEARS_BACK),
    end: "",
  };
}

/** Distinct series keys of a resolved selection, in selection order. */
export function gridKeys(resolved) {
  const keys = [];
  for (const r of resolved) if (!keys.includes(r.series_key)) keys.push(r.series_key);
  return keys;
}

/**
 * The POST /market/grid body, or null when there is nothing to ask for.
 * No request is ever built for an empty selection.
 */
export function buildGridBody(selection, catalog, controls) {
  const keys = gridKeys(resolveSelection(selection, catalog));
  if (keys.length === 0) return null;
  const body = {
    keys,
    anchor: controls.anchor,
    mode: controls.mode,
    frequency: controls.frequency,
  };
  if (controls.end) body.end = controls.end;
  return body;
}

/**
 * Trailing-edge debounce: a burst of calls inside `wait` ms runs `fn` once,
 * with the LAST call's arguments.
 */
export function createDebouncer(fn, wait = GRID_DEBOUNCE_MS, timers = globalThis) {
  let handle = null;
  return {
    call(...args) {
      if (handle !== null) timers.clearTimeout(handle);
      handle = timers.setTimeout(() => {
        handle = null;
        fn(...args);
      }, wait);
    },
    cancel() {
      if (handle !== null) timers.clearTimeout(handle);
      handle = null;
    },
  };
}

/**
 * Only the newest request may render. Take a token before each request; a
 * response whose token is no longer the latest is discarded.
 */
export function createLatestGate() {
  let latest = 0;
  return {
    next() {
      latest += 1;
      return latest;
    },
    isLatest(token) {
      return token === latest;
    },
  };
}
