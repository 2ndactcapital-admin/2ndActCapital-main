/**
 * Chart settings, requests and interaction timing (mkt04b). Pure: no React, no
 * DOM, no fetch. Timers and animation frames are injected so tests drive them.
 *
 * The chart asks for raw series ONCE per (selected keys, resolution): one GET
 * /api/market/series per burst of changes (debounced), only the newest
 * response renders, nothing is requested for an empty selection, and more keys
 * than the server's limit are refused before anything is sent. Moving the
 * anchor or switching the measure never refetches: rebase.mjs re-measures the
 * same points in the browser.
 *
 * Identity never travels here: the URL carries only keys and frequency.
 */

import { NO_ENVELOPE_MESSAGE, errorMessage, grantsRead } from "./catalogModel.mjs";
import { MEASURE_INDEX, MEASURE_SIGMA, SCALE_LOG } from "./chartContract.mjs";
import { GRID_DEBOUNCE_MS, createDebouncer, createLatestGate, gridKeys, yearsAgo } from "./gridRequest.mjs";
import { DEFAULT_ANCHOR_YEARS_BACK, DEFAULT_FREQUENCY } from "./marketDefaults.mjs";
import { limitMessage, resolveSelection, selectionLimit } from "./selection.mjs";

export const SERIES_ROUTE = "/api/market/series";
export const KEY_DATES_ROUTE = "/api/market/key-dates";
export const CHART_DEBOUNCE_MS = GRID_DEBOUNCE_MS;
export const CHART_MEASURES = [MEASURE_INDEX, MEASURE_SIGMA];
export const PAGE_STEP = 12;

export const SERIES_LOAD_FAILED = "The chart data could not be loaded.";
export const KEY_DATES_UNAVAILABLE = "Regimes and key dates could not be shown: the response did not confirm your access.";
export const NO_RESOLUTION_MESSAGE = "The server offers no resolution for the chart.";

function isObject(v) {
  return v !== null && typeof v === "object" && !Array.isArray(v);
}

function vocabList(list) {
  return Array.isArray(list) ? list.filter((v) => isObject(v) && typeof v.key === "string") : [];
}

/** The server's modes that are chart measures, in the server's order. */
export function chartMeasureOptions(vocab) {
  return vocabList(vocab?.modes).filter((m) => CHART_MEASURES.includes(m.key));
}

/** The series endpoint's frequencies (vocabularies.frequencies), as served. */
export function resolutionOptions(vocab) {
  return vocabList(vocab?.frequencies);
}

/** Chart settings on first load; every option is checked against the server. */
export function initialChartSettings(catalog, today) {
  const vocab = catalog?.vocabularies ?? {};
  const measures = chartMeasureOptions(vocab).map((m) => m.key);
  const freqs = resolutionOptions(vocab).map((f) => f.key);
  return {
    measure: measures.includes(MEASURE_INDEX) ? MEASURE_INDEX : measures[0] ?? null,
    scale: SCALE_LOG,
    resolution: freqs.includes(DEFAULT_FREQUENCY) ? DEFAULT_FREQUENCY : freqs[0] ?? null,
    anchor: yearsAgo(today, DEFAULT_ANCHOR_YEARS_BACK),
    overlays: { events: true, band: true, emphasis: true },
  };
}

/**
 * The GET /api/market/series request for a selection, or:
 *   null                      nothing to ask for (empty selection)
 *   {blocked: true, message}  refused before sending (over the server limit,
 *                             or no resolution the server offers)
 *   {url, keys}               the request
 */
export function buildSeriesRequest(selection, catalog, resolution) {
  const keys = gridKeys(resolveSelection(selection, catalog));
  if (keys.length === 0) return null;
  const limit = selectionLimit(catalog);
  if (keys.length > limit) return { blocked: true, message: limitMessage(limit) };
  if (!resolutionOptions(catalog?.vocabularies).some((f) => f.key === resolution)) {
    return { blocked: true, message: NO_RESOLUTION_MESSAGE };
  }
  const qs = new URLSearchParams({ keys: keys.join(","), frequency: resolution });
  return { url: `${SERIES_ROUTE}?${qs.toString()}`, keys };
}

/**
 * {ok, body} of GET /api/market/series -> {kind: "ready", response} or
 * {kind: "error", message}. FAIL CLOSED: a 200 without a permissions envelope
 * that grants read, or without a series list, is an error and draws no chart.
 */
export function interpretSeries({ ok, body }) {
  if (!ok) return { kind: "error", message: errorMessage(body, SERIES_LOAD_FAILED) };
  if (!grantsRead(body) || !Array.isArray(body.series)) return { kind: "error", message: NO_ENVELOPE_MESSAGE };
  return { kind: "ready", response: body };
}

/**
 * {ok, body} of GET /api/market/key-dates -> {kind: "ready", keyDates,
 * regimes, vocabularies} or {kind: "error", message}. Same fail-closed rule:
 * no envelope, no overlays.
 */
export function interpretKeyDates({ ok, body }) {
  if (!ok) return { kind: "error", message: errorMessage(body, KEY_DATES_UNAVAILABLE) };
  if (!grantsRead(body) || !Array.isArray(body.key_dates) || !Array.isArray(body.regimes) || !isObject(body.vocabularies)) {
    return { kind: "error", message: KEY_DATES_UNAVAILABLE };
  }
  return { kind: "ready", keyDates: body.key_dates, regimes: body.regimes, vocabularies: body.vocabularies };
}

/**
 * Debounced, newest-wins series loading.
 *   fetchJson(url) -> Promise<{ok, body}>
 *   onResult({kind, response | message, url}) — only for the newest request.
 */
export function createSeriesLoader({ fetchJson, onResult, timers = globalThis, wait = CHART_DEBOUNCE_MS }) {
  const gate = createLatestGate();
  const debouncer = createDebouncer(
    async (url) => {
      const token = gate.next();
      let state;
      try {
        state = interpretSeries(await fetchJson(url));
      } catch {
        state = { kind: "error", message: SERIES_LOAD_FAILED };
      }
      if (gate.isLatest(token)) onResult({ ...state, url });
    },
    wait,
    timers,
  );
  return {
    request(url) {
      debouncer.call(url);
    },
    /** Drop any pending request and orphan any in flight. */
    cancel() {
      debouncer.cancel();
      gate.next();
    },
  };
}

/**
 * The anchor slider's keyboard: arrows move one period, Page Up / Page Down
 * twelve, Home / End to the ends. Returns the new index, or null for a key the
 * slider does not handle.
 */
export function stepAnchorIndex(index, key, count) {
  if (count <= 0) return null;
  const last = count - 1;
  const clampTo = (i) => Math.min(Math.max(i, 0), last);
  switch (key) {
    case "ArrowRight":
    case "ArrowUp":
      return clampTo(index + 1);
    case "ArrowLeft":
    case "ArrowDown":
      return clampTo(index - 1);
    case "PageUp":
      return clampTo(index + PAGE_STEP);
    case "PageDown":
      return clampTo(index - PAGE_STEP);
    case "Home":
      return 0;
    case "End":
      return last;
    default:
      return null;
  }
}

/**
 * Coalesce a stream of calls onto animation frames: many schedule(v) calls
 * between two frames run fn ONCE, with the latest v. Used for anchor dragging
 * and hover, so lines are recomputed per frame, not per pointer event.
 */
export function createFrameScheduler(fn, raf = globalThis.requestAnimationFrame, caf = globalThis.cancelAnimationFrame) {
  let handle = null;
  let latest;
  return {
    schedule(v) {
      latest = v;
      if (handle !== null) return;
      handle = raf(() => {
        handle = null;
        fn(latest);
      });
    },
    cancel() {
      if (handle !== null && caf) caf(handle);
      handle = null;
    },
  };
}
