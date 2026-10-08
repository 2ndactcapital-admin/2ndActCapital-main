/**
 * Chart scales, periods and ticks (mkt04b). Pure: no React, no DOM.
 *
 * With rebase.mjs, one of exactly two modules that parse the API's strings
 * (here: ISO dates into milliseconds). Floats are for plotting only.
 *
 *   periods   the chart's x positions: one per period of the chosen resolution
 *             (weekly, monthly, quarterly bucket by period end; native and
 *             daily keep each observation date). A period end past the latest
 *             observation is pulled back to it, so the axis never runs ahead of
 *             the data. The anchor bar snaps to these.
 *   y range   auto-fit to the visible values with 6% padding on each side,
 *             clamped: log index 1..10,000 (1/100 to 100 times the anchor),
 *             linear index -1,000..100,000, sigma -6..+6.
 *   ticks     log: a fixed 1-2-5 ladder pruned to at most eight; linear: the
 *             smallest 1-2-5 step giving at most eight; time: every 5 years,
 *             finer when the span is short.
 */

import { FREQ_MONTHLY, FREQ_QUARTERLY, FREQ_WEEKLY } from "./chartContract.mjs";

export const Y_PADDING = 0.06;
export const MAX_Y_TICKS = 8;
export const LOG_INDEX_CLAMP = [100 / 100, 100 * 100];
export const LINEAR_INDEX_CLAMP = [-10 * 100, 1000 * 100];
export const SIGMA_CLAMP = [-6, 6];
// Kinds of vertical axis (chart-internal names, not server values).
export const AXIS_LOG_INDEX = "log-index";
export const AXIS_LINEAR_INDEX = "linear-index";
export const AXIS_SIGMA = "sigma-axis";
export const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

const ISO = /^(\d{4})-(\d{2})-(\d{2})$/;
const DAY_MS = 86400000;
const YEAR_MS = 365.2425 * DAY_MS;
const pad2 = (n) => String(n).padStart(2, "0");

// ── Dates ───────────────────────────────────────────────────────────────────

/** "YYYY-MM-DD" -> UTC milliseconds, or null. */
export function isoToMs(iso) {
  const m = ISO.exec(String(iso ?? ""));
  if (!m) return null;
  return Date.UTC(Number(m[1]), Number(m[2]) - 1, Number(m[3]));
}

export function msToIso(ms) {
  const d = new Date(ms);
  return `${String(d.getUTCFullYear()).padStart(4, "0")}-${pad2(d.getUTCMonth() + 1)}-${pad2(d.getUTCDate())}`;
}

/** "2021-09-30" -> "Sep 2021". */
export function monthLabel(iso) {
  const m = ISO.exec(String(iso ?? ""));
  return m ? `${MONTHS[Number(m[2]) - 1]} ${m[1]}` : "";
}

/** The period end containing iso for a request frequency. */
export function periodEnd(iso, frequency) {
  const m = ISO.exec(iso);
  if (!m) return iso;
  const y = Number(m[1]);
  const mo = Number(m[2]);
  if (frequency === FREQ_MONTHLY) return msToIso(Date.UTC(y, mo, 0));
  if (frequency === FREQ_QUARTERLY) return msToIso(Date.UTC(y, Math.ceil(mo / 3) * 3, 0));
  if (frequency === FREQ_WEEKLY) {
    // ISO weeks run Monday..Sunday: the period ends on the Sunday on or after.
    const ms = Date.UTC(y, mo - 1, Number(m[3]));
    const dow = new Date(ms).getUTCDay();
    return msToIso(ms + ((7 - dow) % 7) * DAY_MS);
  }
  return iso;
}

/**
 * The chart's periods for parsed series at a frequency, ascending:
 * [{date, ms}]. Every observation falls in exactly one period.
 */
export function buildPeriods(seriesList, frequency) {
  let maxDate = null;
  const ends = new Set();
  for (const s of seriesList) {
    for (const d of s.dates) {
      ends.add(periodEnd(d, frequency));
      if (maxDate === null || d > maxDate) maxDate = d;
    }
  }
  const dates = [...ends].map((d) => (maxDate !== null && d > maxDate ? maxDate : d));
  return [...new Set(dates)].sort().map((date) => ({ date, ms: isoToMs(date) }));
}

// ── Number text ─────────────────────────────────────────────────────────────

/** Fixed decimals with comma grouping of the integer part (display only). */
export function formatNumber(n, decimals = 0) {
  const [int, frac] = Math.abs(n).toFixed(decimals).split(".");
  const sign = n < 0 && Number(int) + Number(frac ?? 0) !== 0 ? "-" : "";
  return `${sign}${int.replace(/\B(?=(\d{3})+(?!\d))/g, ",")}${frac ? `.${frac}` : ""}`;
}

/** "+18.4" / "-3.0": an explicit sign for changes. */
export function signed(n, decimals) {
  const text = formatNumber(n, decimals);
  return text.startsWith("-") || Number(text.replace(/,/g, "")) === 0 ? text : `+${text}`;
}

// ── Y range ─────────────────────────────────────────────────────────────────

/** Axis space: log10 for a log axis (positive values only), identity else. */
export function toAxis(v, log) {
  if (v === null || !Number.isFinite(v)) return null;
  if (!log) return v;
  return v > 0 ? Math.log10(v) : null;
}

export function fromAxis(a, log) {
  return log ? 10 ** a : a;
}

/**
 * [lo, hi] in VALUE space for the visible values.
 *   kind: AXIS_LOG_INDEX | AXIS_LINEAR_INDEX | AXIS_SIGMA
 */
export function yDomain(values, kind) {
  const log = kind === AXIS_LOG_INDEX;
  const clamp = kind === AXIS_LOG_INDEX ? LOG_INDEX_CLAMP : kind === AXIS_SIGMA ? SIGMA_CLAMP : LINEAR_INDEX_CLAMP;
  const cLo = toAxis(clamp[0], log);
  const cHi = toAxis(clamp[1], log);
  let lo = Infinity;
  let hi = -Infinity;
  for (const v of values) {
    const a = toAxis(v, log);
    if (a === null) continue;
    if (a < lo) lo = a;
    if (a > hi) hi = a;
  }
  if (lo === Infinity) {
    // Nothing visible: a neutral window around the anchor.
    [lo, hi] = kind === AXIS_SIGMA ? [-2, 2] : log ? [Math.log10(50), Math.log10(200)] : [50, 150];
  } else {
    const span = hi - lo;
    const padding = span > 0 ? span * Y_PADDING : log ? 0.05 : Math.max(Math.abs(hi) * 0.01, 0.5);
    lo -= padding;
    hi += padding;
  }
  lo = Math.max(lo, cLo);
  hi = Math.min(hi, cHi);
  if (!(hi > lo)) {
    // Everything sat outside the clamp on one side: show the clamp edge.
    if (lo >= cHi) [lo, hi] = [cHi - (cHi - cLo) * Y_PADDING, cHi];
    else [lo, hi] = [cLo, cLo + (cHi - cLo) * Y_PADDING];
  }
  return [fromAxis(lo, log), fromAxis(hi, log)];
}

// ── Pixel mapping ───────────────────────────────────────────────────────────

/** Linear map [d0, d1] -> [r0, r1], both ways. */
export function linearMap(d0, d1, r0, r1) {
  const k = d1 === d0 ? 0 : (r1 - r0) / (d1 - d0);
  return {
    toPx: (v) => r0 + (v - d0) * k,
    fromPx: (px) => (k === 0 ? d0 : d0 + (px - r0) / k),
  };
}

/** Vertical scale: value -> pixel (top is the domain's high end). */
export function makeYScale([lo, hi], log, top, bottom) {
  const m = linearMap(toAxis(lo, log), toAxis(hi, log), bottom, top);
  return {
    toPx: (v) => {
      const a = toAxis(v, log);
      return a === null ? null : m.toPx(a);
    },
    fromPx: (px) => fromAxis(m.fromPx(px), log),
  };
}

export function makeXScale(t0, t1, left, right) {
  return linearMap(t0, t1, left, right);
}

// ── Ticks ───────────────────────────────────────────────────────────────────

const clean = (x) => Number(x.toPrecision(12));

/** Linear ticks: the smallest 1-2-5 step that gives at most `max` ticks. */
export function linearTicks(lo, hi, max = MAX_Y_TICKS) {
  const span = hi - lo;
  if (!(span > 0)) return [lo];
  let k = Math.floor(Math.log10(span / max));
  for (let guard = 0; guard < 60; guard += 1, k += 1) {
    for (const m of [1, 2, 5]) {
      const step = m * 10 ** k;
      const first = Math.ceil(lo / step - 1e-9);
      const last = Math.floor(hi / step + 1e-9);
      if (last - first + 1 <= max) {
        const out = [];
        for (let i = first; i <= last; i += 1) out.push(clean(i * step));
        return out;
      }
    }
  }
  return [lo, hi];
}

/** Every 1-2-5 ladder value in [lo, hi] for the given mantissas. */
export function logLadder(lo, hi, mantissas = [1, 2, 5]) {
  const out = [];
  for (let k = Math.floor(Math.log10(lo)) - 1; k <= Math.ceil(Math.log10(hi)); k += 1) {
    for (const m of mantissas) {
      const v = clean(m * 10 ** k);
      if (v >= lo && v <= hi) out.push(v);
    }
  }
  return out;
}

/**
 * Log ticks from the fixed ladder, pruned to at most `max`: drop the 2s, then
 * the 5s, then keep every second (third, ...) decade. A range too narrow for
 * two ladder values falls back to linear ticks.
 */
export function logTicks(lo, hi, max = MAX_Y_TICKS) {
  for (const mantissas of [[1, 2, 5], [1, 5], [1]]) {
    const t = logLadder(lo, hi, mantissas);
    if (t.length < 2) return linearTicks(lo, hi, max);
    if (t.length <= max) return t;
  }
  const decades = logLadder(lo, hi, [1]);
  for (let every = 2; ; every += 1) {
    const t = decades.filter((v) => Math.round(Math.log10(v)) % every === 0);
    if (t.length <= max) return t;
  }
}

/**
 * Time ticks for [t0, t1]: Jan 1 every 5 years, every 2 years under 15
 * years, every year under 6, and every quarter under 2.
 */
export function timeTicks(t0, t1) {
  const years = (t1 - t0) / YEAR_MS;
  const out = [];
  const y0 = new Date(t0).getUTCFullYear();
  const y1 = new Date(t1).getUTCFullYear();
  if (years < 2) {
    for (let y = y0; y <= y1; y += 1) {
      for (const mo of [0, 3, 6, 9]) {
        const ms = Date.UTC(y, mo, 1);
        if (ms >= t0 && ms <= t1) out.push({ ms, label: `${MONTHS[mo]} ${y}` });
      }
    }
    return out;
  }
  const step = years >= 15 ? 5 : years >= 6 ? 2 : 1;
  for (let y = Math.ceil(y0 / step) * step; y <= y1; y += step) {
    const ms = Date.UTC(y, 0, 1);
    if (ms >= t0 && ms <= t1) out.push({ ms, label: String(y) });
  }
  return out;
}
