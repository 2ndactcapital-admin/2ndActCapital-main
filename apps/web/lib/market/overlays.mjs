/**
 * Cohort band, outlier fences, big moves and the 12-month direction (mkt04b).
 * Pure: no React, no DOM. Everything here works in AXIS space (log10 of the
 * index on a log axis, the value itself otherwise), so "distance from the
 * pack" means what the reader sees on the chart.
 *
 *   band        25th / 50th / 75th percentiles across the series that have a
 *               value at each period; a period needs BAND_MIN_SERIES of them.
 *   fences      at the latest date, q25 - margin and q75 + margin, where margin
 *               = max(0.75 * IQR, 4% of the axis span). Needs PACK_MIN_SERIES
 *               series; with fewer every status is "few".
 *   big moves   |period change| / the standard deviation of that series' own
 *               period changes, above BIG_MOVE_Z; the TOP three per series.
 *   direction   the latest value against the value twelve months earlier.
 */

export const BAND_MIN_SERIES = 3;
export const PACK_MIN_SERIES = 5;
export const FENCE_IQR_MULTIPLE = 0.75;
export const FENCE_FLOOR_SHARE = 0.04;
export const BIG_MOVE_Z = 3;
export const BIG_MOVES_PER_SERIES = 3;

export const STATUS_ABOVE = "above";
export const STATUS_BELOW = "below";
export const STATUS_IN = "in";
export const STATUS_FEW = "few";

const isNum = (v) => typeof v === "number" && Number.isFinite(v);

/** Quantile of an ASCENDING array, linear interpolation (R type 7). */
export function quantile(sorted, p) {
  if (sorted.length === 0) return null;
  const h = (sorted.length - 1) * p;
  const lo = Math.floor(h);
  const hi = Math.ceil(h);
  return sorted[lo] + (h - lo) * (sorted[hi] - sorted[lo]);
}

/**
 * matrix: one array per series of axis values (number|null) per period.
 * Returns one {q25, q50, q75, n} per period, or null where fewer than
 * BAND_MIN_SERIES series have a value.
 */
export function cohortBand(matrix, minSeries = BAND_MIN_SERIES) {
  const periods = matrix.reduce((n, row) => Math.max(n, row.length), 0);
  const out = [];
  for (let p = 0; p < periods; p += 1) {
    const vals = [];
    for (const row of matrix) if (isNum(row[p])) vals.push(row[p]);
    if (vals.length < minSeries) {
      out.push(null);
      continue;
    }
    vals.sort((a, b) => a - b);
    out.push({ q25: quantile(vals, 0.25), q50: quantile(vals, 0.5), q75: quantile(vals, 0.75), n: vals.length });
  }
  return out;
}

/** The pack's fences at the latest date, or null with too few series. */
export function packFences(latest, axisSpan, minSeries = PACK_MIN_SERIES) {
  const vals = latest.filter(isNum).sort((a, b) => a - b);
  if (vals.length < minSeries) return null;
  const q25 = quantile(vals, 0.25);
  const q50 = quantile(vals, 0.5);
  const q75 = quantile(vals, 0.75);
  const margin = Math.max(FENCE_IQR_MULTIPLE * (q75 - q25), FENCE_FLOOR_SHARE * Math.abs(axisSpan));
  return { q25, q50, q75, margin, lo: q25 - margin, hi: q75 + margin };
}

/** "above" | "below" | "in" | "few" for one latest value. */
export function packStatus(value, fences) {
  if (fences === null || !isNum(value)) return STATUS_FEW;
  if (value > fences.hi) return STATUS_ABOVE;
  if (value < fences.lo) return STATUS_BELOW;
  return STATUS_IN;
}

/** Sample standard deviation (n - 1), or null under two values. */
export function sampleSd(vals) {
  if (vals.length < 2) return null;
  const mean = vals.reduce((a, b) => a + b, 0) / vals.length;
  const ss = vals.reduce((a, b) => a + (b - mean) ** 2, 0);
  return Math.sqrt(ss / (vals.length - 1));
}

/**
 * The periods (indexes into `axisValues`) of a series' largest moves: the
 * change from the previous valued period, scored against the standard
 * deviation of all such changes; scores above `threshold`, at most `top`,
 * largest first.
 */
export function bigMoves(axisValues, threshold = BIG_MOVE_Z, top = BIG_MOVES_PER_SERIES) {
  const changes = [];
  let prev = null;
  axisValues.forEach((v, i) => {
    if (!isNum(v)) return;
    if (prev !== null) changes.push({ index: i, change: v - prev });
    prev = v;
  });
  const sd = sampleSd(changes.map((c) => c.change));
  if (!sd) return [];
  return changes
    .map((c) => ({ index: c.index, z: Math.abs(c.change) / sd }))
    .filter((c) => c.z > threshold)
    .sort((a, b) => b.z - a.z || a.index - b.index)
    .slice(0, top);
}

/** {change, direction: "up" | "down" | "flat"} or null when either is missing. */
export function direction(latest, earlier) {
  if (!isNum(latest) || !isNum(earlier)) return null;
  const change = latest - earlier;
  return { change, direction: Math.abs(change) < 1e-12 ? "flat" : change > 0 ? "up" : "down" };
}
