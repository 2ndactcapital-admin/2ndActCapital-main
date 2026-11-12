/**
 * Interpreting a market API response for the page (mkt04a). Pure: no React, no
 * DOM, no fetch.
 *
 * FAIL CLOSED. The page renders its selection and grid controls only when
 * interpretCatalog returns { kind: "ready" }, and that requires a real
 * permissions envelope with can_read === true AND a vocabularies object. A
 * missing envelope, a malformed one, or one that does not grant read is an
 * error state — never a default that quietly shows the controls anyway.
 */

function isObject(v) {
  return v !== null && typeof v === "object" && !Array.isArray(v);
}

/**
 * The server's own message from an error body, unchanged. Handles the three
 * shapes that reach the page: {error} (the Next.js route's own 401/400/502),
 * {detail: "text"} (FastAPI HTTPException), and the market API's refusal
 * {detail: {message, errors: [{msg}]}}.
 */
export function errorMessage(body, fallback) {
  if (isObject(body)) {
    if (typeof body.error === "string" && body.error) return body.error;
    const d = body.detail;
    if (typeof d === "string" && d) return d;
    if (isObject(d)) {
      const msgs = Array.isArray(d.errors)
        ? d.errors.map((e) => (isObject(e) ? e.msg : null)).filter((m) => typeof m === "string" && m)
        : [];
      const head = typeof d.message === "string" ? d.message : "";
      if (head && msgs.length) return `${head}: ${msgs.join("; ")}`;
      if (head) return head;
      if (msgs.length) return msgs.join("; ");
    }
    if (Array.isArray(d)) {
      const msgs = d.map((e) => (isObject(e) ? e.msg : null)).filter((m) => typeof m === "string" && m);
      if (msgs.length) return msgs.join("; ");
    }
  }
  return fallback;
}

export const NO_ENVELOPE_MESSAGE =
  "Market data could not be shown: the response did not confirm your access.";

/** True only for an envelope that genuinely grants read. No fallback. */
export function grantsRead(body) {
  return isObject(body) && isObject(body.permissions) && body.permissions.can_read === true;
}

/**
 * { ok, body } of GET /api/market/catalog -> the page state.
 *   { kind: "error", message } | { kind: "ready", catalog }
 */
export function interpretCatalog({ ok, body }) {
  if (!ok) {
    return { kind: "error", message: errorMessage(body, "Market data could not be loaded.") };
  }
  if (!grantsRead(body) || !isObject(body.vocabularies)) {
    return { kind: "error", message: NO_ENVELOPE_MESSAGE };
  }
  const catalog = {
    categories: Array.isArray(body.categories) ? body.categories : [],
    indicators: Array.isArray(body.indicators) ? body.indicators : [],
    securities: Array.isArray(body.securities) ? body.securities : [],
    permissions: body.permissions,
    vocabularies: body.vocabularies,
  };
  return { kind: "ready", catalog };
}

/** A machine code as display text ("no_price_history" -> "no price history"). */
export function codeText(code) {
  return String(code ?? "").replace(/_/g, " ");
}

/**
 * Look a code up in a server vocabulary of [{key, label}] if the response
 * publishes one; otherwise the code text itself. Nothing is invented.
 */
export function vocabText(vocabList, code) {
  if (Array.isArray(vocabList)) {
    const hit = vocabList.find((v) => isObject(v) && v.key === code);
    if (hit && typeof hit.label === "string") return hit.label;
  }
  return codeText(code);
}

// ── Security search (dailyops1) ─────────────────────────────────────────────
// The catalog lists only the SELECTABLE securities; the rest of the security
// master is found through GET /api/market/securities/search?q=. The limits
// (minimum / maximum characters, result cap) come from the catalog's own
// vocabularies.search — with no such vocabulary the search box is not shown.

export const SECURITY_SEARCH_ROUTE = "/api/market/securities/search";

/** The catalog's search limits, or null (fail closed: no search box). */
export function searchSettings(catalog) {
  const s = catalog?.vocabularies?.search;
  if (!isObject(s)) return null;
  const { min_chars: minChars, max_chars: maxChars, max_results: maxResults } = s;
  if (![minChars, maxChars, maxResults].every((n) => typeof n === "number" && n > 0)) return null;
  return { minChars, maxChars, maxResults };
}

/** The request URL for a search, or null when the text is too short / long. */
export function securitySearchUrl(text, settings) {
  const q = String(text ?? "").trim();
  if (!settings || q.length < settings.minChars || q.length > settings.maxChars) return null;
  return `${SECURITY_SEARCH_ROUTE}?q=${encodeURIComponent(q)}`;
}

export const SEARCH_FAILED = "The security search could not be completed.";

/**
 * { ok, body } of GET /api/market/securities/search -> the search state.
 *   { kind: "error", message } | { kind: "results", query, securities }
 * FAIL CLOSED: a response without a read-granting envelope is an error.
 */
export function interpretSecuritySearch({ ok, body }) {
  if (!ok) return { kind: "error", message: errorMessage(body, SEARCH_FAILED) };
  if (!grantsRead(body) || !Array.isArray(body.securities)) {
    return { kind: "error", message: NO_ENVELOPE_MESSAGE };
  }
  return { kind: "results", query: typeof body.query === "string" ? body.query : "", securities: body.securities };
}
