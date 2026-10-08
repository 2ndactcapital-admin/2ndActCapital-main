/**
 * "What moves with it" — the correlations panel (mkt04c). Pure: no React, no
 * DOM, no fetch. Timers are injected so tests drive them.
 *
 * The focus is one of the selected series; the candidates are the others. The
 * window is the chart's anchor to its end (the server's today unless a view
 * set an end). One POST /api/market/correlations per burst of changes
 * (debounced, so a dragged anchor asks once it stops), only the newest
 * response renders, nothing is sent with fewer than two series, and a response
 * without an envelope granting read is an error with no results.
 *
 * r arrives as a string ("0.8312", "-0.0450") and is shown exactly as
 * returned. It is NEVER parsed: the bar's width is the same digits read as a
 * percentage by moving the decimal point in the text (rBarWidth), and the sign
 * is the leading "-". The server's order (|r| descending, nulls last) is kept.
 */

import { NO_ENVELOPE_MESSAGE, errorMessage, grantsRead, vocabText } from "./catalogModel.mjs";
import { GRID_DEBOUNCE_MS, createDebouncer, createLatestGate, gridKeys } from "./gridRequest.mjs";
import { resolveSelection, selectionLimit } from "./selection.mjs";

export const CORRELATIONS_ROUTE = "/api/market/correlations";
export const CORRELATION_DEBOUNCE_MS = GRID_DEBOUNCE_MS;
export const CORRELATIONS_FAILED = "Correlations could not be computed.";
export const SIGN_POSITIVE = "positive";
export const SIGN_NEGATIVE = "negative";

const R_TEXT = /^-?(\d)\.(\d+)$/;

function isObject(v) {
  return v !== null && typeof v === "object" && !Array.isArray(v);
}

/** The selected series that can be compared: distinct series keys, in selection order, with their labels. */
export function correlationSeries(selection, catalog) {
  const out = [];
  for (const r of resolveSelection(selection, catalog)) {
    if (!out.some((x) => x.key === r.series_key)) out.push({ key: r.series_key, label: r.label });
  }
  return out;
}

/** The current focus if it is still selected, else the first selected series. */
export function chooseFocus(current, series) {
  if (series.some((s) => s.key === current)) return current;
  return series[0]?.key ?? null;
}

/** The server's lag range [lo, hi] (catalog vocabularies.limits.lag_months), or null. */
export function lagRange(catalog) {
  const r = catalog?.vocabularies?.limits?.lag_months;
  if (!Array.isArray(r) || r.length !== 2 || !Number.isInteger(r[0]) || !Number.isInteger(r[1]) || r[0] > r[1]) return null;
  return [r[0], r[1]];
}

/** A whole lag inside the server's range; 0 when no range is published (the server's default). */
export function clampLag(lag, range) {
  if (!range) return 0;
  const n = Number.isFinite(lag) ? Math.trunc(lag) : 0;
  return Math.min(Math.max(n, range[0]), range[1]);
}

/** Every whole lag the server accepts, as dropdown options [{value, label}] ("+3", "0", "-3"). */
export function lagOptions(range) {
  if (!range) return [{ value: 0, label: "0" }];
  const out = [];
  for (let n = range[0]; n <= range[1]; n += 1) out.push({ value: n, label: n > 0 ? `+${n}` : `${n}` });
  return out;
}

/**
 * The POST body, or null when there is nothing to compare: fewer than two
 * series, no anchor, or no key limit from the server. The focus is never a
 * candidate; at most the server's key limit is sent; the lag is clamped.
 */
export function buildCorrelationRequest({ selection, catalog, focus, lag, anchor, end }) {
  const series = correlationSeries(selection, catalog);
  if (series.length < 2 || typeof anchor !== "string" || anchor === "") return null;
  const focusKey = chooseFocus(focus, series);
  const limit = selectionLimit(catalog);
  const keys = gridKeys(series.map((s) => ({ series_key: s.key }))).filter((k) => k !== focusKey).slice(0, limit);
  if (keys.length === 0) return null;
  const body = { focus_key: focusKey, keys, anchor, lag_months: clampLag(lag, lagRange(catalog)) };
  if (typeof end === "string" && end !== "") body.end = end;
  return { url: CORRELATIONS_ROUTE, body, key: JSON.stringify(body) };
}

/**
 * {ok, body} -> {kind: "ready", response} | {kind: "error", message}.
 * FAIL CLOSED: a 200 without an envelope granting read, or without a results
 * list, is an error and shows no results.
 */
export function interpretCorrelations({ ok, body }) {
  if (!ok) return { kind: "error", message: errorMessage(body, CORRELATIONS_FAILED) };
  if (!grantsRead(body) || !Array.isArray(body.results)) return { kind: "error", message: NO_ENVELOPE_MESSAGE };
  return { kind: "ready", response: body };
}

/**
 * Debounced, newest-wins loading.
 *   fetchJson(url, body) -> Promise<{ok, body}>
 *   onResult({kind, response | message, key}) — only for the newest request.
 */
export function createCorrelationLoader({ fetchJson, onResult, timers = globalThis, wait = CORRELATION_DEBOUNCE_MS }) {
  const gate = createLatestGate();
  const debouncer = createDebouncer(
    async (req) => {
      const token = gate.next();
      let state;
      try {
        state = interpretCorrelations(await fetchJson(req.url, req.body));
      } catch {
        state = { kind: "error", message: CORRELATIONS_FAILED };
      }
      if (gate.isLatest(token)) onResult({ ...state, key: req.key });
    },
    wait,
    timers,
  );
  return {
    request(req) {
      debouncer.call(req);
    },
    /** Drop any pending request and orphan any in flight. */
    cancel() {
      debouncer.cancel();
      gate.next();
    },
  };
}

// ── Display ─────────────────────────────────────────────────────────────────

/**
 * The bar width for an r string, as a CSS percentage of the full bar: |r| of
 * 1 fills it. Text only — "0.8312" -> "83.12%", "-0.0450" -> "4.50%",
 * "1.0000" -> "100%". Anything else (null, junk) -> "0%".
 */
export function rBarWidth(r) {
  const m = R_TEXT.exec(typeof r === "string" ? r : "");
  if (!m) return "0%";
  if (m[1] !== "0") return /^0*$/.test(m[2]) && m[1] === "1" ? "100%" : "0%";
  const whole = m[2].slice(0, 2).padEnd(2, "0").replace(/^0/, "");
  const frac = m[2].slice(2);
  return `${whole}${frac ? `.${frac}` : ""}%`;
}

/** "positive" | "negative" from the text's sign; null for a null r. */
export function rSign(r) {
  if (typeof r !== "string" || !R_TEXT.test(r)) return null;
  return r.startsWith("-") ? SIGN_NEGATIVE : SIGN_POSITIVE;
}

/**
 * Display rows in the server's order. Names come from the selection (the
 * catalog's names); the frequency label from the catalog's frequencies; a
 * null r carries the reason as text.
 */
export function correlationRows(response, { series, frequencies, reasons } = {}) {
  const names = new Map((series ?? []).map((s) => [s.key, s.label]));
  return (Array.isArray(response?.results) ? response.results : []).filter(isObject).map((row) => {
    const hasR = typeof row.r === "string";
    return {
      key: row.series_key,
      name: names.get(row.series_key) ?? row.series_key,
      r: hasR ? row.r : null,
      width: hasR ? rBarWidth(row.r) : "0%",
      sign: hasR ? rSign(row.r) : null,
      n: row.n,
      frequency: typeof row.frequency === "string" ? vocabText(frequencies, row.frequency) : "",
      reason: hasR ? null : vocabText(reasons, row.unavailable_reason),
    };
  });
}
