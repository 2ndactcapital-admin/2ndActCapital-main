// Decimal-as-string display formatting — TA Model Sprint 3.
//
// Every monetary/rate value on the commitment projection screen arrives as a
// JSON string (services.ta_model._fixed — fixed-point, never scientific
// notation). This module formats those strings for display WITHOUT EVER
// calling Number()/parseFloat()/parseInt() on the value itself: both money
// grouping and the rate-to-percent conversion are done by plain string
// manipulation (regex digit-grouping, and shifting the decimal point two
// characters right instead of multiplying by 100). A JS `Number` cannot
// exactly represent every base-10 decimal string (e.g. large paid-in
// cumulative totals, or a rate string that recurs in binary), so any coercion
// through one is a real, if usually invisible, corruption risk this module
// exists to rule out for good — see CLAUDE.md's Decimal-as-string discipline
// and the TA model's own module docstring (services/ta_model.py).
//
// The one place a Number *is* used, deliberately, is `chartFraction` below —
// pixel/SVG geometry has no display precision requirement, so it is not
// "the rendering path" this file's no-float-coercion guarantee covers.

const DECIMAL_STRING = /^(-?)(\d+)(?:\.(\d+))?$/;

function parts(value) {
  if (value == null || value === "") return null;
  const m = DECIMAL_STRING.exec(String(value).trim());
  if (!m) return null;
  const [, sign, intPart, fracPart = ""] = m;
  return { sign, intPart, fracPart };
}

function grouped(intPart) {
  return intPart.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
}

/** "1234567.8" -> "$1,234,567.80". Not a plain decimal string -> shown verbatim. */
export function formatMoneyExact(value) {
  const p = parts(value);
  if (!p) return value == null || value === "" ? "—" : String(value);
  const cents = (p.fracPart + "00").slice(0, 2);
  return `${p.sign === "-" ? "-" : ""}$${grouped(p.intPart)}.${cents}`;
}

/** Shift a decimal string's point RIGHT by `places` — exact, no multiplication. */
function shiftRight(p, places) {
  let frac = p.fracPart.padEnd(places, "0");
  const moved = frac.slice(0, places);
  const remaining = frac.slice(places);
  let intPart = (p.intPart + moved).replace(/^0+(?=\d)/, "");
  if (intPart === "") intPart = "0";
  return { sign: p.sign, intPart, fracPart: remaining };
}

function trimTrailingZeros(fracPart) {
  return fracPart.replace(/0+$/, "");
}

/** "0.078800" (a per-period rate) -> "7.88%" — exact, string-only. */
export function formatRateExact(value) {
  const p = parts(value);
  if (!p) return value == null || value === "" ? "—" : String(value);
  const pct = shiftRight(p, 2);
  const frac = trimTrailingZeros(pct.fracPart);
  const body = frac ? `${pct.intPart}.${frac}` : pct.intPart;
  return `${pct.sign === "-" ? "-" : ""}${grouped(body.split(".")[0])}${frac ? "." + frac : ""}%`;
}

/** A plain count/years figure ("10" or "2.5") — grouped, no rounding. */
export function formatNumberExact(value) {
  const p = parts(value);
  if (!p) return value == null || value === "" ? "—" : String(value);
  const frac = trimTrailingZeros(p.fracPart);
  return `${p.sign === "-" ? "-" : ""}${grouped(p.intPart)}${frac ? "." + frac : ""}`;
}

/**
 * A value's position within [0, max] as a plain JS number for SVG geometry
 * ONLY (bar heights, line coordinates) — never used to produce display text.
 * Precision loss here can only mis-place a pixel, never mis-state a number
 * the user reads.
 */
export function chartFraction(value, max) {
  const n = Number(value);
  const m = Number(max);
  if (!Number.isFinite(n) || !Number.isFinite(m) || m <= 0) return 0;
  return Math.max(0, Math.min(1, n / m));
}
