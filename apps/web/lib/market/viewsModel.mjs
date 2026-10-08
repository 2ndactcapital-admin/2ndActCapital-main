/**
 * Saved views (mkt04c). Pure: no React, no DOM, no fetch.
 *
 * A view is the chart's state in the canonical config (v 1,
 * docs/MARKET_DATA_DESIGN_V1.md "Config schema"):
 *   {v, selection: [{kind, key}], anchor, end, mode, scale,
 *    overlays: {events, band, emphasis}}
 * buildViewConfig emits exactly those fields — nothing else. The anchor is
 * stored RESOLVED: a date, or "N years ago" when the page's anchor is still the
 * relative one ("5 years ago" or a loaded relative view), never a key date.
 *
 * applyViewConfig is the reverse: it returns the next selection and chart
 * settings and never modifies the view. Keys the server lists as unavailable,
 * or that the catalog no longer allows, are skipped and named in a notice. A
 * stored mode that is not a chart measure leaves the current measure in place,
 * with a note. The resolution and the Grid tab's controls are not part of a
 * view.
 */

import { NO_ENVELOPE_MESSAGE, errorMessage, grantsRead, vocabText } from "./catalogModel.mjs";
import { SCALE_LINEAR, SCALE_LOG } from "./chartContract.mjs";
import { chartMeasureOptions, initialChartSettings } from "./chartRequest.mjs";
import { refusalMessage } from "./customDates.mjs";
import { yearsAgo } from "./gridRequest.mjs";
import { DEFAULT_ANCHOR_YEARS_BACK } from "./marketDefaults.mjs";
import { KIND_INDICATOR, isAllowed, selectionLimit } from "./selection.mjs";
import { ANCHOR_DATE, ANCHOR_RELATIVE, CONFIG_VERSION } from "./viewContract.mjs";

export const VIEWS_ROUTE = "/api/market/views";
export const VIEWS_LOAD_FAILED = "Saved views could not be loaded.";
export const VIEW_SAVE_FAILED = "The view could not be saved.";
export const VIEW_DELETE_FAILED = "The view could not be deleted.";
export const CONFIG_FIELDS = ["v", "selection", "anchor", "end", "mode", "scale", "overlays"];
export const OVERLAY_KEYS = ["events", "band", "emphasis"];

function isObject(v) {
  return v !== null && typeof v === "object" && !Array.isArray(v);
}

export function viewUrl(id) {
  return `${VIEWS_ROUTE}/${encodeURIComponent(String(id))}`;
}

// ── Page settings ───────────────────────────────────────────────────────────

/**
 * The chart settings plus the two fields a view also carries: anchorYears
 * (the anchor is "N years ago" while set) and end (a stored end, or null).
 */
export function initialPageSettings(catalog, today) {
  return { ...initialChartSettings(catalog, today), anchorYears: DEFAULT_ANCHOR_YEARS_BACK, end: null };
}

/** Apply a chart-settings patch: any anchor change not itself relative makes the anchor a plain date. */
export function patchSettings(prev, patch) {
  const next = { ...prev, ...patch };
  if ("anchor" in patch && !("anchorYears" in patch)) next.anchorYears = null;
  return next;
}

/** A stored end as a date, or null (the server's today). */
export function resolveEnd(end, today) {
  if (!isObject(end)) return null;
  if (end.type === ANCHOR_DATE) return typeof end.value === "string" ? end.value : null;
  if (end.type === ANCHOR_RELATIVE && Number.isInteger(end.years)) return yearsAgo(today, end.years);
  return null;
}

function copyAnchor(a) {
  if (!isObject(a)) return null;
  if (a.type === ANCHOR_DATE) return { type: ANCHOR_DATE, value: a.value };
  if (a.type === ANCHOR_RELATIVE) return { type: ANCHOR_RELATIVE, years: a.years };
  return null;
}

// ── Build ───────────────────────────────────────────────────────────────────

/** The canonical config for the current selection and settings. Exactly CONFIG_FIELDS. */
export function buildViewConfig(selection, settings) {
  const overlays = {};
  for (const k of OVERLAY_KEYS) overlays[k] = settings.overlays?.[k] === true;
  return {
    v: CONFIG_VERSION,
    selection: (selection ?? []).map((s) => ({ kind: s.kind, key: s.key })),
    anchor: Number.isInteger(settings.anchorYears)
      ? { type: ANCHOR_RELATIVE, years: settings.anchorYears }
      : { type: ANCHOR_DATE, value: settings.anchor },
    end: copyAnchor(settings.end),
    mode: settings.measure,
    scale: settings.scale,
    overlays,
  };
}

export function createViewRequest(name, config) {
  return { url: VIEWS_ROUTE, method: "POST", body: { name, config } };
}

/** Update = PUT the current settings; the name is left as it is. */
export function updateViewRequest(id, config) {
  return { url: viewUrl(id), method: "PUT", body: { config } };
}

export function deleteViewRequest(id) {
  return { url: viewUrl(id), method: "DELETE" };
}

// ── Apply ───────────────────────────────────────────────────────────────────

function selectionLabel(catalog, kind, key) {
  const list = kind === KIND_INDICATOR ? catalog?.indicators : catalog?.securities;
  const hit = (list ?? []).find((x) => (kind === KIND_INDICATOR ? x?.series_key === key : x?.id === key));
  return typeof hit?.name === "string" ? hit.name : key;
}

/**
 * view {config, unavailable} + {catalog, today, settings (current)} ->
 *   {selection, settings, skipped: [names], measureKept: null | {stored, kept}}
 * The view object is never modified.
 */
export function applyViewConfig(view, { catalog, today, settings }) {
  const config = isObject(view?.config) ? view.config : {};
  const unavailable = new Set(Array.isArray(view?.unavailable) ? view.unavailable : []);
  const limit = selectionLimit(catalog);

  const selection = [];
  const skipped = [];
  for (const s of Array.isArray(config.selection) ? config.selection : []) {
    if (!isObject(s)) continue;
    if (selection.some((x) => x.kind === s.kind && x.key === s.key)) continue;
    if (unavailable.has(s.key) || !isAllowed(catalog, s.kind, s.key) || selection.length >= limit) {
      skipped.push(selectionLabel(catalog, s.kind, s.key));
      continue;
    }
    selection.push({ kind: s.kind, key: s.key });
  }

  const next = { ...settings };
  const a = config.anchor;
  if (isObject(a) && a.type === ANCHOR_RELATIVE && Number.isInteger(a.years)) {
    next.anchor = yearsAgo(today, a.years);
    next.anchorYears = a.years;
  } else if (isObject(a) && a.type === ANCHOR_DATE && typeof a.value === "string") {
    next.anchor = a.value;
    next.anchorYears = null;
  }
  next.end = copyAnchor(config.end);

  let measureKept = null;
  const measures = chartMeasureOptions(catalog?.vocabularies).map((m) => m.key);
  if (measures.includes(config.mode)) next.measure = config.mode;
  else if (typeof config.mode === "string") measureKept = { stored: config.mode, kept: settings.measure };

  if (config.scale === SCALE_LOG || config.scale === SCALE_LINEAR) next.scale = config.scale;
  if (isObject(config.overlays)) {
    next.overlays = {};
    for (const k of OVERLAY_KEYS) next.overlays[k] = config.overlays[k] === true;
  }
  return { selection, settings: next, skipped, measureKept };
}

/** The quiet notices for a loaded view, with server labels for the measures. */
export function viewNotices(applied, modes) {
  const out = [];
  if (applied.skipped.length) out.push(`Not available any more, left out: ${applied.skipped.join(", ")}`);
  if (applied.measureKept) {
    out.push(
      `This view was saved with ${vocabText(modes, applied.measureKept.stored)}, which the chart does not draw; ` +
        `it stays on ${vocabText(modes, applied.measureKept.kept)}.`,
    );
  }
  return out;
}

// ── The list ────────────────────────────────────────────────────────────────

/**
 * {ok, body} of GET /api/market/views -> {kind: "ready", presets, views,
 * permissions, limits} or {kind: "error", message}. FAIL CLOSED: no envelope
 * granting read, no list.
 */
export function interpretViews({ ok, body }) {
  if (!ok) return { kind: "error", message: errorMessage(body, VIEWS_LOAD_FAILED) };
  if (!grantsRead(body) || !Array.isArray(body.presets) || !Array.isArray(body.views)) {
    return { kind: "error", message: NO_ENVELOPE_MESSAGE };
  }
  return {
    kind: "ready",
    presets: body.presets,
    views: body.views,
    permissions: body.permissions,
    limits: isObject(body.limits) ? body.limits : {},
  };
}

export function canWriteViews(state) {
  return state?.kind === "ready" && state.permissions?.can_write === true;
}

/** Presets first, then the caller's own; only the caller's own can be updated or deleted. */
export function viewEntries(state) {
  if (state?.kind !== "ready") return [];
  const writable = canWriteViews(state);
  const entry = (v, preset) => ({ id: v.id, name: v.name, view: v, preset, canDelete: !preset && writable, canUpdate: !preset && writable });
  return [...state.presets.map((v) => entry(v, true)), ...state.views.map((v) => entry(v, false))];
}

export function viewNameMaxLength(state) {
  const n = state?.limits?.name_max;
  return Number.isInteger(n) && n > 0 ? n : undefined;
}

export function deleteViewConfirmText(name) {
  return `Delete the view "${name}"?`;
}

// ── Save form ───────────────────────────────────────────────────────────────

export const EMPTY_SAVE = Object.freeze({ name: "", saving: false, error: null });

export function canSaveView(form) {
  return !form.saving && form.name.trim() !== "";
}

/** A write's result -> {ok, message, row}. A refusal carries the server's message verbatim. */
export function writeOutcome({ ok, body }, fallback) {
  if (ok) return { ok: true, message: null, row: isObject(body) ? body : null };
  return { ok: false, message: refusalMessage(body, fallback), row: null };
}
