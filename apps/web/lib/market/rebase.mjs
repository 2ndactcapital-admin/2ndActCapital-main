/**
 * Client-side rebasing for the chart (mkt04b). Pure: no React, no DOM.
 *
 * One of exactly two modules that turn the API's value strings into numbers
 * (the other is scales.mjs). The numbers are floats FOR PLOTTING ONLY: the Grid
 * tab never parses a value, and nothing computed here is ever sent back.
 *
 * The definitions are the canonical ones (docs/MARKET_DATA_DESIGN_V1.md,
 * "Transform definitions"; apps/api/services/market_data/transforms.py):
 *   as-of(date)  the value of the last observation on or before the date.
 *   v0           as-of(anchor); if none, the first observation, and the series
 *                is FLOATING. Also floating when the anchor precedes the
 *                series' first observation over its whole history.
 *   index        100 * v / v0; needs v0 > 0, else 'non_positive_anchor'. A
 *                series whose default_transform is 'level' still indexes but
 *                carries 'rate_like_series_indexed'.
 *   sigma        (v - v0) / sd, sd = the server's `stddev` field (full-history
 *                stddev_samp). null or 0 is 'zero_variance'. The browser never
 *                computes a standard deviation: one computed from a downsampled
 *                series differs from the server's.
 * tests/market/golden_rebase.json (written by the production Python transforms)
 * pins every case to within the server's 6-decimal rounding.
 */

import {
  MEASURE_INDEX,
  MEASURE_SIGMA,
  REASON_NON_POSITIVE_ANCHOR,
  REASON_NO_OBSERVATIONS,
  REASON_ZERO_VARIANCE,
  TRANSFORM_LEVEL,
  WARNING_RATE_LIKE,
} from "./chartContract.mjs";

/** A decimal string as a float, or null when it is not a finite number. */
export function toNumber(text) {
  if (text === null || text === undefined || text === "") return null;
  const n = Number(text);
  return Number.isFinite(n) ? n : null;
}

/**
 * One series object of GET /market/series as parsed arrays. Points keep their
 * raw string (for display) beside the float. Unparseable points are dropped.
 */
export function parseSeries(s) {
  const dates = [];
  const values = [];
  const raw = [];
  for (const p of Array.isArray(s?.points) ? s.points : []) {
    if (!Array.isArray(p) || typeof p[0] !== "string") continue;
    const v = toNumber(p[1]);
    if (v === null) continue;
    dates.push(p[0]);
    values.push(v);
    raw.push(String(p[1]));
  }
  return {
    key: s?.series_key ?? null,
    frequency: s?.frequency ?? null,
    firstObservationDate: typeof s?.first_observation_date === "string" ? s.first_observation_date : null,
    lastObservationDate: typeof s?.last_observation_date === "string" ? s.last_observation_date : null,
    stddev: toNumber(s?.stddev),
    dates,
    values,
    raw,
  };
}

/** Index of the last date <= iso (ISO strings compare as dates), or -1. */
export function asOfIndex(dates, iso) {
  let lo = 0;
  let hi = dates.length;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (dates[mid] <= iso) lo = mid + 1;
    else hi = mid;
  }
  return lo - 1;
}

/** The as-of value at iso, or null before the first observation. */
export function asOf(series, iso) {
  const i = asOfIndex(series.dates, iso);
  return i >= 0 ? series.values[i] : null;
}

/** v0 and the floating flag, exactly as transforms.anchor_of. */
export function anchorOf(series, anchorIso) {
  let floating = series.firstObservationDate !== null && anchorIso < series.firstObservationDate;
  let i = asOfIndex(series.dates, anchorIso);
  if (i < 0 && series.dates.length > 0) {
    i = 0;
    floating = true;
  }
  if (i < 0) return { value: null, date: null, floating };
  return { value: series.values[i], date: series.dates[i], floating };
}

/**
 * Everything needed to measure one series from one anchor:
 *   { measure, v0, anchorDate, floating, sd, unavailable, warnings }
 * `unavailable` is a server reason code or null.
 */
export function rebaseSpec(series, { measure, anchor, defaultTransform }) {
  if (measure !== MEASURE_INDEX && measure !== MEASURE_SIGMA) {
    throw new Error(`not a chart measure: ${measure}`);
  }
  const a = anchorOf(series, anchor);
  const spec = {
    measure,
    v0: a.value,
    anchorDate: a.date,
    floating: a.floating,
    sd: series.stddev,
    unavailable: null,
    warnings: [],
  };
  if (a.value === null) {
    spec.unavailable = REASON_NO_OBSERVATIONS;
  } else if (measure === MEASURE_INDEX) {
    if (!(a.value > 0)) spec.unavailable = REASON_NON_POSITIVE_ANCHOR;
    else if (defaultTransform === TRANSFORM_LEVEL) spec.warnings.push(WARNING_RATE_LIKE);
  } else if (spec.sd === null || spec.sd === 0) {
    spec.unavailable = REASON_ZERO_VARIANCE;
  }
  return spec;
}

/** One raw value measured by a spec (null when the spec is unavailable). */
export function measureValue(spec, v) {
  if (spec.unavailable !== null || v === null) return null;
  if (spec.measure === MEASURE_INDEX) return (100 * v) / spec.v0;
  return (v - spec.v0) / spec.sd;
}

/** The measured value as of iso: measureValue(spec, asOf(series, iso)). */
export function rebaseAt(series, spec, iso) {
  return measureValue(spec, asOf(series, iso));
}
